from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
from typing import AsyncIterator, Awaitable, Callable, cast

from sirius_chat.config import SessionConfig, TokenUsageRecord
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest, LLMProvider
from sirius_chat.memory import (
    EventMemoryFileStore,
    EventMemoryManager,
    UserMemoryFileStore,
    UserProfile,
    SelfMemoryFileStore,
    SelfMemoryManager,
)
from sirius_chat.async_engine.utils import (
    record_task_stat,
    estimate_tokens,
    extract_json_payload,
    normalize_multimodal_inputs,
)
from sirius_chat.async_engine.prompts import build_system_prompt
from sirius_chat.exceptions import OrchestrationConfigError
from sirius_chat.skills.registry import SkillRegistry
from sirius_chat.skills.executor import SkillExecutor, parse_skill_calls, strip_skill_calls
from sirius_chat.skills.models import SkillChainContext
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer
from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer
from sirius_chat.core.engagement import EngagementCoordinator, EngagementDecision
from sirius_chat.background_tasks import BackgroundTaskConfig, BackgroundTaskManager
from sirius_chat.token.store import TokenUsageStore
logger = logging.getLogger(__name__)


from contextlib import asynccontextmanager
from typing import AsyncIterator


@asynccontextmanager
async def _noop_semaphore() -> AsyncIterator[None]:
    """No-op async context manager used when concurrency limiting is disabled."""
    yield


@dataclass(slots=True)
class _PendingTurn:
    """Buffered user turn for debounce/batching."""
    participant_user_id: str
    messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class LiveSessionContext:
    file_store: UserMemoryFileStore
    event_file_store: EventMemoryFileStore
    event_store: EventMemoryManager
    task_token_usage: dict[str, int] = field(default_factory=dict)
    user_message_count_since_extract: dict[str, int] = field(default_factory=dict)
    known_by_id: dict[str, Participant] = field(default_factory=dict)
    known_by_label: dict[str, str] = field(default_factory=dict)
    pending_turn: _PendingTurn | None = None
    skill_registry: SkillRegistry | None = None
    skill_executor: SkillExecutor | None = None
    event_bus: SessionEventBus = field(default_factory=SessionEventBus)
    bg_task_manager: BackgroundTaskManager | None = None
    token_store: TokenUsageStore | None = None
    self_memory: SelfMemoryManager = field(default_factory=SelfMemoryManager)
    self_memory_store: SelfMemoryFileStore | None = None
    self_memory_turn_counter: int = 0  # AI reply count since last self-memory extract
    llm_semaphore: asyncio.Semaphore | None = None  # Concurrency limiter for LLM calls


@dataclass(slots=True)
class AsyncRolePlayEngine:
    provider: LLMProvider | AsyncLLMProvider
    _live_session_contexts: dict[int, LiveSessionContext] = field(default_factory=dict, init=False, repr=False)
    _orchestration_log_cache: set[str] = field(default_factory=set, init=False, repr=False)

    _TASK_MEMORY_EXTRACT = "memory_extract"
    _TASK_EVENT_EXTRACT = "event_extract"
    _TASK_MEMORY_MANAGER = "memory_manager"
    _TASK_INTENT_ANALYSIS = "intent_analysis"
    _TASK_TIMEOUT_SECONDS_DEFAULT = 45.0
    _TASK_TIMEOUT_SECONDS_CHAT_MAIN = 90.0
    _SUPPORTED_MULTIMODAL_TYPES = {"image", "video", "audio", "text"}
    _MEMORY_METADATA_LINE_PATTERNS = (
        re.compile(
            r"^\s*置信度\s*[：:]\s*\d+(?:\.\d+)?%\s*\|\s*类型\s*[：:]\s*[^|]+\|\s*来源\s*[：:]\s*[^|]+\|\s*时间\s*[：:]\s*[^|]+\|\s*内容\s*[：:]\s*.+$"
        ),
        re.compile(
            r"^\s*confidence\s*:\s*\d+(?:\.\d+)?%\s*\|\s*type\s*:\s*[^|]+\|\s*source\s*:\s*[^|]+\|\s*time\s*:\s*[^|]+\|\s*content\s*:\s*.+$",
            re.IGNORECASE,
        ),
    )
    _MEMORY_METADATA_CN_LABEL_PATTERNS = (
        re.compile(r"置信度\s*[：:]"),
        re.compile(r"类型\s*[：:]"),
        re.compile(r"来源\s*[：:]"),
        re.compile(r"时间\s*[：:]"),
        re.compile(r"内容\s*[：:]"),
    )
    _MEMORY_METADATA_EN_LABEL_PATTERNS = (
        re.compile(r"confidence\s*:", re.IGNORECASE),
        re.compile(r"type\s*:", re.IGNORECASE),
        re.compile(r"source\s*:", re.IGNORECASE),
        re.compile(r"time\s*:", re.IGNORECASE),
        re.compile(r"content\s*:", re.IGNORECASE),
    )
    
    # 所有需要模型支持的必需任务
    _REQUIRED_TASKS = [
        "memory_extract",
        "event_extract",
    ]

    def validate_orchestration_config(self, config: SessionConfig) -> None:
        """验证多模型协同配置的完整性。
        
        多模型协同必需启用，支持两种配置方案：
        1. unified_model: 所有任务使用同一个模型
        2. task_models: 为每个任务独立配置模型
        
        所有任务默认启用，可通过 task_enabled 字典禁用特定任务。
        
        Args:
            config: 会话配置
            
        Raises:
            OrchestrationConfigError: 如果配置不完整或冲突
        """
        orchestration = config.orchestration
        
        # 方案1：已经在 OrchestrationPolicy.validate() 中执行基本检查
        # 这里进行运行时补充验证
        
        if orchestration.unified_model:
            # 方案1：所有任务使用 unified_model
            log_key = f"unified:{orchestration.unified_model.strip()}"
            if log_key not in self._orchestration_log_cache:
                logger.info(
                    f"多模型协同（方案1）：所有任务共用模型 '{orchestration.unified_model}'"
                )
                self._orchestration_log_cache.add(log_key)
            return
        
        if orchestration.task_models:
            # 方案2：按任务配置模型，但仅检查启用的任务
            enabled_tasks = [
                task for task in self._REQUIRED_TASKS
                if orchestration.task_enabled.get(task, True)  # 检查启用的任务
            ]
            
            missing_tasks = [
                task for task in enabled_tasks
                if not orchestration.task_models.get(task)
            ]
            if missing_tasks:
                raise OrchestrationConfigError(
                    {task: [task] for task in missing_tasks}
                )
            enabled_flag = {
                task: bool(orchestration.task_enabled.get(task, True))
                for task in self._REQUIRED_TASKS
            }
            log_key = (
                "task_models:"
                f"{json.dumps(dict(sorted(orchestration.task_models.items())), ensure_ascii=False, sort_keys=True)}|"
                f"{json.dumps(enabled_flag, ensure_ascii=False, sort_keys=True)}"
            )
            if log_key not in self._orchestration_log_cache:
                logger.info(
                    f"多模型协同（方案2）：按任务配置模型 - {orchestration.task_models}"
                )
                self._orchestration_log_cache.add(log_key)
            return

    def _get_task_timeout_seconds(self, task_name: str) -> float:
        if task_name == "chat_main":
            return self._TASK_TIMEOUT_SECONDS_CHAT_MAIN
        return self._TASK_TIMEOUT_SECONDS_DEFAULT
    
    def get_model_for_task(self, config: SessionConfig, task_name: str) -> str:
        """根据多模型协同配置获取任务模型。
        
        Args:
            config: 会话配置
            task_name: 任务名称（如 'memory_extract'、'event_extract'）
            
        Returns:
            该任务应使用的模型名称
            
        Raises:
            ValueError: 如果无法确定任务模型
        """
        orchestration = config.orchestration
        
        # 优先返回统一模型（方案1）
        if orchestration.unified_model:
            return orchestration.unified_model
        
        # 其次返回按任务配置的模型（方案2）
        model = orchestration.task_models.get(task_name)
        if model:
            return model
        
        # 不应该到达这里，因为已在验证时检查
        raise ValueError(
            f"无法获取任务 '{task_name}' 的模型。请检查 OrchestrationPolicy 配置。"
        )

    def _prepare_transcript(self, config: SessionConfig, transcript: Transcript | None) -> Transcript:
        if transcript is not None:
            return transcript
        prepared = Transcript()
        prepared.add(Message(role="system", content=config.global_system_prompt))
        return prepared

    def _get_or_create_live_context(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
    ) -> LiveSessionContext:
        key = id(transcript)
        existing = self._live_session_contexts.get(key)
        if existing is not None:
            return existing

        file_store = UserMemoryFileStore(config.work_path)
        event_file_store = EventMemoryFileStore(config.work_path)
        event_store = event_file_store.load()
        transcript.user_memory.merge_from(file_store.load_all())

        known_by_id: dict[str, Participant] = {}
        known_by_label: dict[str, str] = {}
        for user_id, entry in transcript.user_memory.entries.items():
            profile = entry.profile
            participant = Participant(
                name=profile.name,
                user_id=profile.user_id,
                persona=profile.persona,
                identities=dict(profile.identities),
                aliases=list(profile.aliases),
                traits=list(profile.traits),
                metadata=dict(profile.metadata),
            )
            known_by_id[user_id] = participant
            labels = [participant.name, participant.user_id, *participant.aliases]
            for label in labels:
                if label:
                    known_by_label[label.strip().lower()] = participant.user_id

        created = LiveSessionContext(
            file_store=file_store,
            event_file_store=event_file_store,
            event_store=event_store,
            known_by_id=known_by_id,
            known_by_label=known_by_label,
            token_store=TokenUsageStore(
                config.work_path / "token_usage.db",
                session_id=str(config.work_path),
            ),
        )

        # Load AI self-memory (diary + glossary)
        if config.orchestration.enable_self_memory:
            self_store = SelfMemoryFileStore(config.work_path)
            created.self_memory = self_store.load()
            created.self_memory_store = self_store
            # Apply diary decay on load
            removed = created.self_memory.apply_diary_decay()
            if removed > 0:
                logger.info("自我记忆日记衰退：移除 %d 条过期条目", removed)

        skills_dir = config.work_path / "skills"
        SkillRegistry.ensure_skills_directory(skills_dir)

        # Initialize skill system if enabled
        if config.orchestration.enable_skills:
            registry = SkillRegistry()
            loaded_count = registry.load_from_directory(
                skills_dir,
                auto_install_deps=config.orchestration.auto_install_skill_deps,
            )
            if loaded_count > 0:
                created.skill_registry = registry
                created.skill_executor = SkillExecutor(config.work_path)
                logger.info("SKILL系统已初始化，已加载 %d 个SKILL", loaded_count)
            else:
                logger.debug("SKILL系统已启用但未找到任何SKILL文件: %s", skills_dir)
        else:
            logger.debug("SKILL系统已禁用，但已初始化SKILL目录: %s", skills_dir)

        # ── LLM concurrency semaphore ──
        max_concurrent = int(config.orchestration.max_concurrent_llm_calls)
        if max_concurrent > 0:
            created.llm_semaphore = asyncio.Semaphore(max_concurrent)

        # ── Background tasks: consolidation + self-memory ──
        needs_bg = config.orchestration.consolidation_enabled
        if needs_bg:
            bg_config = BackgroundTaskConfig(
                consolidation_enabled=config.orchestration.consolidation_enabled,
                consolidation_interval_seconds=config.orchestration.consolidation_interval_seconds,
                consolidation_min_entries=config.orchestration.consolidation_min_entries,
                consolidation_min_notes=config.orchestration.consolidation_min_notes,
                consolidation_min_facts=config.orchestration.consolidation_min_facts,
                self_memory_enabled=False,
                self_memory_interval_seconds=0,
                compression_enabled=False,
                cleanup_enabled=False,
            )
            bg_manager = BackgroundTaskManager(config=bg_config)

            if config.orchestration.consolidation_enabled:
                async def _consolidation_callback(
                    _engine: AsyncRolePlayEngine = self,
                    _config: SessionConfig = config,
                    _transcript: Transcript = transcript,
                    _ctx: LiveSessionContext = created,
                ) -> None:
                    """Periodically consolidate events + notes + facts for all users."""
                    class _Adapter:
                        def __init__(self, engine: AsyncRolePlayEngine) -> None:
                            self._engine = engine
                        async def generate_async(self, request: GenerationRequest) -> str:
                            return await self._engine._call_provider(request)

                    adapter = _Adapter(_engine)
                    try:
                        model = _engine.get_model_for_task(_config, _engine._TASK_EVENT_EXTRACT)
                    except ValueError:
                        model = _config.agent.model

                    # Consolidate events
                    for uid in _ctx.event_store.get_all_user_ids():
                        await _ctx.event_store.consolidate_entries(
                            user_id=uid,
                            provider_async=adapter,
                            model_name=model,
                            min_entries=bg_config.consolidation_min_entries,
                        )

                    # Consolidate user summaries and facts
                    for uid in list(_transcript.user_memory.entries.keys()):
                        await _transcript.user_memory.consolidate_summary_notes(
                            user_id=uid,
                            provider_async=adapter,
                            model_name=model,
                            min_notes=bg_config.consolidation_min_notes,
                        )
                        await _transcript.user_memory.consolidate_memory_facts(
                            user_id=uid,
                            provider_async=adapter,
                            model_name=model,
                            min_facts=bg_config.consolidation_min_facts,
                        )

                    # Persist after consolidation
                    _ctx.file_store.save_all(_transcript.user_memory)
                    _ctx.event_file_store.save(_ctx.event_store)

                    # Diary decay during consolidation
                    if _ctx.self_memory_store is not None:
                        removed = _ctx.self_memory.apply_diary_decay()
                        if removed > 0:
                            logger.info("后台归纳：移除 %d 条衰退日记条目", removed)
                        _ctx.self_memory_store.save(_ctx.self_memory)

                bg_manager.set_consolidation_callback(_consolidation_callback)

            created.bg_task_manager = bg_manager

        self._live_session_contexts[key] = created
        return created

    def _ensure_skill_runtime(
        self,
        *,
        config: SessionConfig,
        context: LiveSessionContext,
    ) -> None:
        """Ensure SKILL runtime is available for the current session context.

        This covers context-reuse cases where the context was created before
        SKILL files existed or before ``enable_skills`` became True.
        """
        if not config.orchestration.enable_skills:
            return

        skills_dir = config.work_path / "skills"
        SkillRegistry.ensure_skills_directory(skills_dir)

        registry = context.skill_registry
        executor = context.skill_executor

        # Initialize missing components, then (re)load skill files.
        if registry is None:
            registry = SkillRegistry()
            context.skill_registry = registry
        if executor is None:
            executor = SkillExecutor(config.work_path)
            context.skill_executor = executor

        if not registry.all_skills():
            loaded_count = registry.load_from_directory(
                skills_dir,
                auto_install_deps=config.orchestration.auto_install_skill_deps,
            )
            if loaded_count > 0:
                logger.info("SKILL系统懒加载完成，已加载 %d 个SKILL", loaded_count)
            else:
                logger.debug("SKILL系统已启用，但当前未加载到任何SKILL: %s", skills_dir)

    @staticmethod
    def _build_known_entities(known_by_id: dict[str, Participant]) -> list[str]:
        known_entities: list[str] = []
        for item in known_by_id.values():
            values = [item.name, item.user_id, *item.aliases]
            for value in values:
                text = value.strip()
                if text and text not in known_entities:
                    known_entities.append(text)
        return known_entities

    @staticmethod
    def _resolve_session_reply_mode(config: SessionConfig) -> str:
        mode = str(config.orchestration.session_reply_mode or "auto").strip().lower()
        if mode == "smart":
            return "auto"
        if mode in {"silent", "none", "no_reply"}:
            return "never"
        if mode in {"always", "never", "auto"}:
            return mode
        return "auto"

    def _resolve_participant_for_turn(
        self,
        *,
        transcript: Transcript,
        turn: Message,
        context: LiveSessionContext,
    ) -> Participant:
        speaker = str(turn.speaker or "")
        normalized = speaker.strip().lower()
        participant: Participant | None = None

        if turn.channel and turn.channel_user_id:
            mapped_user_id = transcript.user_memory.resolve_user_id(
                channel=turn.channel,
                external_user_id=turn.channel_user_id,
            )
            if mapped_user_id:
                participant = context.known_by_id.get(mapped_user_id)
                if participant is None and mapped_user_id in transcript.user_memory.entries:
                    profile = transcript.user_memory.entries[mapped_user_id].profile
                    participant = Participant(
                        name=profile.name,
                        user_id=profile.user_id,
                        persona=profile.persona,
                        identities=dict(profile.identities),
                        aliases=list(profile.aliases),
                        traits=list(profile.traits),
                        metadata=dict(profile.metadata),
                    )
                    context.known_by_id[participant.user_id] = participant

        resolved_id = context.known_by_label.get(normalized)
        if resolved_id and participant is None:
            participant = context.known_by_id.get(resolved_id)
        if participant is None:
            memory_user_id = transcript.user_memory.resolve_user_id(speaker=turn.speaker)
            if memory_user_id:
                participant = context.known_by_id.get(memory_user_id)
                if participant is None and memory_user_id in transcript.user_memory.entries:
                    profile = transcript.user_memory.entries[memory_user_id].profile
                    participant = Participant(
                        name=profile.name,
                        user_id=profile.user_id,
                        persona=profile.persona,
                        identities=dict(profile.identities),
                        aliases=list(profile.aliases),
                        traits=list(profile.traits),
                        metadata=dict(profile.metadata),
                    )
                    context.known_by_id[participant.user_id] = participant
        if participant is None:
            identities = {}
            if turn.channel and turn.channel_user_id:
                identities[turn.channel] = turn.channel_user_id
            participant = Participant(name=speaker, user_id=speaker, identities=identities)
            context.known_by_id[participant.user_id] = participant

        labels = [participant.name, participant.user_id, *participant.aliases]
        for label in labels:
            if label:
                context.known_by_label[label.strip().lower()] = participant.user_id
        return participant

    async def _finalize_and_persist_live_context(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        context: LiveSessionContext,
    ) -> None:
        # Stop background tasks gracefully
        if context.bg_task_manager is not None:
            await context.bg_task_manager.stop()

        # 最终化事件记忆：对积累的事件进行 LLM 验证
        try:
            class ProviderAdapter:
                def __init__(self, engine: AsyncRolePlayEngine) -> None:
                    self.engine = engine

                async def generate_async(self, request: GenerationRequest) -> str:
                    return await self.engine._call_provider(request)

            finalize_result = await context.event_store.finalize_pending_events(
                provider_async=ProviderAdapter(self),
                model_name=config.agent.model,
                min_mentions=3,
            )
            logger.info(
                "事件记忆最终化完成 - 已验证: %s, 已拒绝: %s, 待验证: %s",
                finalize_result["verified_count"],
                finalize_result["rejected_count"],
                finalize_result["pending_count"],
            )
        except Exception as e:
            logger.warning(f"事件记忆最终化失败，继续执行: {e}")

        context.file_store.save_all(transcript.user_memory)
        context.event_file_store.save(context.event_store)
        if context.self_memory_store is not None:
            context.self_memory_store.save(context.self_memory)
        if context.skill_executor is not None:
            context.skill_executor.save_all_stores()

    @staticmethod
    def _parse_runtime_datetime(raw: str) -> datetime | None:
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _record_task_stat(
        self, transcript: Transcript, task_name: str, metric: str, increment: int = 1
    ) -> None:
        """Record a task statistic in the transcript."""
        record_task_stat(transcript, task_name, metric, increment)

    def _build_system_prompt(
        self,
        config: SessionConfig,
        transcript: Transcript,
        skill_descriptions: str = "",
        environment_context: str = "",
        skip_sections: list[str] | None = None,
        diary_section: str = "",
        glossary_section: str = "",
    ) -> str:
        """Delegate to the prompts module for system prompt building."""
        return build_system_prompt(
            config, transcript,
            skill_descriptions=skill_descriptions,
            environment_context=environment_context,
            skip_sections=skip_sections or [],
            diary_section=diary_section,
            glossary_section=glossary_section,
        )

    async def _call_provider(self, request_payload: GenerationRequest) -> str:
        generate_async = getattr(self.provider, "generate_async", None)
        if callable(generate_async):
            async_fn = cast(Callable[[GenerationRequest], Awaitable[str]], generate_async)
            return await async_fn(request_payload)
        generate_sync = getattr(self.provider, "generate", None)
        if not callable(generate_sync):
            raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")
        sync_fn = cast(Callable[[GenerationRequest], str], generate_sync)
        return await asyncio.to_thread(sync_fn, request_payload)

    async def _call_provider_with_retry(
        self,
        *,
        request_payload: GenerationRequest,
        retry_times: int,
        transcript: Transcript,
        task_name: str,
        actor_id: str,
    ) -> str:
        last_error: RuntimeError | None = None
        attempts = max(1, retry_times + 1)
        timeout_seconds = self._get_task_timeout_seconds(task_name)
        for index in range(attempts):
            try:
                content = await asyncio.wait_for(
                    self._call_provider(request_payload),
                    timeout=timeout_seconds,
                )
                prompt_parts = [request_payload.system_prompt]
                for item in request_payload.messages:
                    message_content = item.get("content", "")
                    if isinstance(message_content, list):
                        prompt_parts.append(
                            " ".join(
                                str(part.get("text", ""))
                                for part in message_content
                                if isinstance(part, dict) and part.get("type") == "text"
                            )
                        )
                    else:
                        prompt_parts.append(str(message_content))
                prompt_text = "\n".join(part for part in prompt_parts if part)
                prompt_tokens = self._estimate_tokens(prompt_text)
                completion_tokens = self._estimate_tokens(content)
                record = TokenUsageRecord(
                    actor_id=actor_id,
                    task_name=task_name,
                    model=request_payload.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    input_chars=len(prompt_text),
                    output_chars=len(content),
                    retries_used=index,
                )
                transcript.add_token_usage_record(record)
                ctx = self._live_session_contexts.get(id(transcript))
                if ctx is not None and ctx.token_store is not None:
                    ctx.token_store.add(record)
                return content
            except asyncio.TimeoutError as exc:
                last_error = RuntimeError(
                    f"提供商调用超时：task={task_name}, model={request_payload.model}, timeout={timeout_seconds:.0f}s"
                )
                if index >= attempts - 1:
                    break
                await asyncio.sleep(min(0.05 * (2**index), 0.3))
            except RuntimeError as exc:
                last_error = exc
                if index >= attempts - 1:
                    break
                await asyncio.sleep(min(0.05 * (2**index), 0.3))
        raise cast(RuntimeError, last_error)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count for text. Delegates to utils module."""
        return estimate_tokens(text)

    @staticmethod
    def _extract_json_payload(raw: str) -> dict[str, object] | None:
        """Extract JSON from raw text. Delegates to utils module."""
        return extract_json_payload(raw)

    async def _run_memory_extract_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        task_token_usage: dict[str, int],
    ) -> None:
        task_name = self._TASK_MEMORY_EXTRACT
        # 检查任务是否启用
        if not config.orchestration.task_enabled.get(task_name, True):
            return  # 任务被禁用
        
        model = self.get_model_for_task(config, task_name)

        self._record_task_stat(transcript, task_name, "attempted")

        system_prompt = (
            "你是用户画像提取器。请从输入中提取 JSON，并严格输出 JSON 对象，"
            "字段仅包含 inferred_persona(string)、inferred_traits(array[string])、"
            "inferred_aliases(array[string])、preference_tags(array[string])、summary_note(string)。"
        )
        task_input = self._build_memory_extract_task_input(
            transcript=transcript,
            participant=participant,
            content=content,
        )
        estimated_cost = self._estimate_tokens(system_prompt + task_input)

        used = task_token_usage.get(task_name, 0)
        budget = int(config.orchestration.task_budgets.get(task_name, 0))
        if budget > 0 and used + estimated_cost > budget:
            self._record_task_stat(transcript, task_name, "skipped_budget")
            return

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": task_input}],
            temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 128)),
            purpose=task_name,
        )

        retry_times = int(config.orchestration.task_retries.get(task_name, 0))
        try:
            raw = await self._call_provider_with_retry(
                request_payload=request_payload,
                retry_times=retry_times,
                transcript=transcript,
                task_name=task_name,
                actor_id=participant.user_id,
            )
        except RuntimeError:
            self._record_task_stat(transcript, task_name, "failed_provider")
            return

        if retry_times > 0:
            self._record_task_stat(transcript, task_name, "retry_enabled")

        task_token_usage[task_name] = used + estimated_cost
        parsed = self._extract_json_payload(raw)
        if parsed is None:
            self._record_task_stat(transcript, task_name, "failed_parse")
            return

        inferred_persona = parsed.get("inferred_persona")
        inferred_aliases = parsed.get("inferred_aliases")
        inferred_traits = parsed.get("inferred_traits")
        preference_tags = parsed.get("preference_tags")
        summary_note = parsed.get("summary_note")

        transcript.user_memory.apply_ai_runtime_update(
            user_id=participant.user_id,
            inferred_persona=str(inferred_persona).strip() if isinstance(inferred_persona, str) else None,
            inferred_aliases=[str(item).strip() for item in inferred_aliases if str(item).strip()]
            if isinstance(inferred_aliases, list)
            else None,
            inferred_traits=[str(item).strip() for item in inferred_traits if str(item).strip()]
            if isinstance(inferred_traits, list)
            else None,
            preference_tags=[str(item).strip() for item in preference_tags if str(item).strip()]
            if isinstance(preference_tags, list)
            else None,
            summary_note=str(summary_note).strip() if isinstance(summary_note, str) else None,
            source="memory_extract",
            confidence=0.8,
        )
        self._record_task_stat(transcript, task_name, "succeeded")

    @staticmethod
    def _normalize_multimodal_inputs(
        multimodal_inputs: list[dict[str, str]],
        *,
        max_items: int,
        max_value_length: int,
    ) -> list[dict[str, str]]:
        """Normalize multimodal inputs. Delegates to utils module."""
        return normalize_multimodal_inputs(
            multimodal_inputs,
            max_items=max_items,
            max_value_length=max_value_length,
            supported_types=AsyncRolePlayEngine._SUPPORTED_MULTIMODAL_TYPES,
        )

    async def _run_self_memory_extract_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        context: LiveSessionContext,
        assistant_content: str,
    ) -> None:
        """Extract diary entries and glossary terms from the conversation.

        Called after the AI generates a reply. Uses LLM to decide what to
        remember (diary) and which terms to define (glossary).
        """
        if not config.orchestration.enable_self_memory:
            return

        task_name = "self_memory_extract"
        model = self.get_model_for_task(config, task_name) if config.orchestration.task_models.get(task_name) else (
            config.orchestration.unified_model or config.agent.model
        )

        # Build recent conversation excerpt for context
        recent_msgs: list[str] = []
        for msg in transcript.messages[-8:]:
            role = msg.role
            speaker = msg.speaker or role
            text = msg.content[:200].replace("\n", " ")
            if text.strip():
                recent_msgs.append(f"[{speaker}] {text}")
        context_text = "\n".join(recent_msgs)

        system_prompt = (
            "你是AI的自省记忆提取器。基于以下对话片段，提取两类记忆：\n"
            "1. diary: AI值得记住的事情（有趣的事、重要决定、情感印象、里程碑）。"
            "每条包含 content(string), importance(0-1), keywords(array), category(reflection|observation|decision|emotion|milestone)。\n"
            "2. glossary: 对话中出现的AI可能不熟悉或值得记录的专有名词/术语。"
            "每条包含 term(string), definition(string), domain(tech|daily|culture|game|custom), confidence(0-1)。\n"
            "严格输出JSON: {\"diary\": [...], \"glossary\": [...]}\n"
            "若无值得记录的内容，返回空数组。保持简洁，每次最多3条diary和5条glossary。"
        )

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": context_text}],
            temperature=0.2,
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 256)),
            purpose=task_name,
        )

        try:
            raw = await self._call_provider_with_retry(
                request_payload=request_payload,
                retry_times=0,
                transcript=transcript,
                task_name=task_name,
                actor_id=config.agent.name,
            )
        except RuntimeError:
            logger.debug("自我记忆提取失败，跳过")
            return

        parsed = self._extract_json_payload(raw)
        if parsed is None:
            return

        from sirius_chat.memory.self.models import DiaryEntry, GlossaryTerm

        # Process diary entries
        diary_items = parsed.get("diary")
        if isinstance(diary_items, list):
            for item in diary_items[:3]:
                if not isinstance(item, dict):
                    continue
                content_text = str(item.get("content", "")).strip()
                if not content_text:
                    continue
                entry = DiaryEntry(
                    content=content_text,
                    importance=float(item.get("importance", 0.5)),
                    keywords=[str(k) for k in item.get("keywords", []) if str(k).strip()],
                    category=str(item.get("category", "observation")),
                    related_user_ids=[],
                )
                context.self_memory.add_diary_entry(entry)

        # Process glossary terms
        glossary_items = parsed.get("glossary")
        if isinstance(glossary_items, list):
            for item in glossary_items[:5]:
                if not isinstance(item, dict):
                    continue
                term_text = str(item.get("term", "")).strip()
                defn = str(item.get("definition", "")).strip()
                if not term_text or not defn:
                    continue
                term = GlossaryTerm(
                    term=term_text,
                    definition=defn,
                    source="conversation",
                    confidence=float(item.get("confidence", 0.6)),
                    domain=str(item.get("domain", "custom")),
                    context_examples=[assistant_content[:80]] if assistant_content.strip() else [],
                )
                context.self_memory.add_or_update_term(term)

        logger.debug(
            "自我记忆提取完成 | diary=%d glossary=%d",
            len(diary_items) if isinstance(diary_items, list) else 0,
            len(glossary_items) if isinstance(glossary_items, list) else 0,
        )

    async def _run_event_extract_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        task_token_usage: dict[str, int],
    ) -> dict[str, object] | None:
        """Legacy per-message event extraction — kept for external callers.

        In v2 the engine uses ``_run_batch_event_extract`` instead.
        """
        return None

    async def _run_batch_event_extract(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        task_token_usage: dict[str, int],
        event_store: EventMemoryManager,
    ) -> list[object]:
        """Batch-extract user observations from buffered messages.

        Observations are directly written into user memory facts.
        """
        task_name = self._TASK_EVENT_EXTRACT
        if not config.orchestration.task_enabled.get(task_name, True):
            return []

        model = self.get_model_for_task(config, task_name)
        self._record_task_stat(transcript, task_name, "attempted")

        # Budget check
        estimated_cost = 512  # conservative estimate for batch extraction
        used = task_token_usage.get(task_name, 0)
        budget = int(config.orchestration.task_budgets.get(task_name, 0))
        if budget > 0 and used + estimated_cost > budget:
            self._record_task_stat(transcript, task_name, "skipped_budget")
            return []

        class _ProviderAdapter:
            def __init__(self, engine: AsyncRolePlayEngine) -> None:
                self._engine = engine

            async def generate_async(self, request: GenerationRequest) -> str:
                return await self._engine._call_provider(request)

        try:
            new_observations = await event_store.extract_observations(
                user_id=participant.user_id,
                user_name=participant.name,
                provider_async=_ProviderAdapter(self),
                model_name=model,
                temperature=float(config.orchestration.task_temperatures.get(task_name, 0.3)),
                max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 512)),
            )
        except Exception as exc:
            logger.warning("批量事件提取失败 (user=%s): %s", participant.user_id, exc)
            self._record_task_stat(transcript, task_name, "failed_provider")
            return []

        task_token_usage[task_name] = used + estimated_cost

        if not new_observations:
            self._record_task_stat(transcript, task_name, "no_observations")
            return []

        # ── 将观察直接写入用户记忆 ──
        category_to_memory = {
            "preference": ("preference_tag", "preference"),
            "trait": ("inferred_trait", "identity"),
            "relationship": ("social_context", "event"),
            "experience": ("summary", "event"),
            "emotion": ("emotional_pattern", "emotion"),
            "goal": ("summary", "event"),
            "custom": ("summary", "custom"),
        }
        for obs in new_observations:
            fact_type, mem_cat = category_to_memory.get(obs.category, ("summary", "custom"))
            transcript.user_memory.add_memory_fact(
                user_id=participant.user_id,
                fact_type=fact_type,
                value=obs.summary,
                source="event_observation",
                confidence=obs.confidence,
                memory_category=mem_cat,
                source_event_id=obs.event_id,
            )

        self._record_task_stat(transcript, task_name, "succeeded")
        logger.info(
            "事件观察提取完成 | user=%s | observations=%d",
            participant.user_id, len(new_observations),
        )
        return list(new_observations)

    async def _run_memory_manager_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        task_token_usage: dict[str, int],
    ) -> None:
        """汇聚、去重、标注、验证用户的记忆事实。"""
        task_name = self._TASK_MEMORY_MANAGER
        model = config.orchestration.memory_manager_model.strip()
        if not model:
            return  # memory_manager 可选

        self._record_task_stat(transcript, task_name, "attempted")

        # 收集当前用户的所有记忆事实
        entry = transcript.user_memory.entries.get(participant.user_id)
        if entry is None or not entry.runtime.memory_facts:
            return

        # 构建记忆列表用于汇聚
        facts_json = [
            {
                "id": i,
                "fact_type": fact.fact_type,
                "value": fact.value,
                "source": fact.source,
                "confidence": fact.confidence,
                "category": fact.memory_category,
            }
            for i, fact in enumerate(entry.runtime.memory_facts)
        ]

        system_prompt = (
            "你是记忆管理器。基于输入的记忆事实列表，执行以下操作：\n"
            "1. 检测重复/相似的事实并合并\n"
            "2. 为每个事实分配类别：identity（身份）、preference（偏好）、emotion（情绪）、event（事件）或 custom（自定义）\n"
            "3. 检测相互冲突的记忆（如：喜欢稳定 vs 喜欢创新）\n"
            "4. 输出结构化的汇聚结果为 JSON 数组，每个元素包含："
            "value、memory_category、is_duplicate、conflict_ids(冲突的id列表)、reason(说明)\n"
            "严格输出 JSON 数组，不要额外文本。"
        )

        task_input = f"记忆事实列表：{json.dumps(facts_json, ensure_ascii=False, indent=2)}"
        estimated_cost = self._estimate_tokens(system_prompt + task_input)

        budget = int(config.orchestration.task_budgets.get(task_name, 0))
        used = task_token_usage.get(task_name, 0)
        if budget > 0 and used + estimated_cost > budget:
            self._record_task_stat(transcript, task_name, "skipped_budget")
            return

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": task_input}],
            temperature=float(config.orchestration.memory_manager_temperature),
            max_tokens=int(config.orchestration.memory_manager_max_tokens),
            purpose=task_name,
        )

        retry_times = int(config.orchestration.task_retries.get(task_name, 0))
        try:
            raw = await self._call_provider_with_retry(
                request_payload=request_payload,
                retry_times=retry_times,
                transcript=transcript,
                task_name=task_name,
                actor_id=participant.user_id,
            )
        except RuntimeError:
            self._record_task_stat(transcript, task_name, "failed_provider")
            return

        if retry_times > 0:
            self._record_task_stat(transcript, task_name, "retry_enabled")

        task_token_usage[task_name] = used + estimated_cost

        # 解析 LLM 输出
        try:
            parsed_list = json.loads(raw)
            if not isinstance(parsed_list, list):
                self._record_task_stat(transcript, task_name, "failed_parse")
                return
        except (json.JSONDecodeError, ValueError):
            self._record_task_stat(transcript, task_name, "failed_parse")
            return

        # 应用汇聚结果：更新 memory_facts
        # 标记重复的记忆，并为所有记忆添加类别和验证标记
        duplicate_indices: set[int] = set()
        for result in parsed_list:
            if not isinstance(result, dict):
                continue
            if result.get("is_duplicate", False):
                # 找到对应的原始记忆并标记为重复
                value = str(result.get("value", "")).strip()
                for idx, fact in enumerate(entry.runtime.memory_facts):
                    if fact.value == value and idx not in duplicate_indices:
                        duplicate_indices.add(idx)
                        break

        # 更新记忆的类别和验证标记
        for i, fact in enumerate(entry.runtime.memory_facts):
            if i in duplicate_indices:
                continue  # 跳过重复的
            # 在结果中找到对应的条目
            for result in parsed_list:
                if str(result.get("value", "")).strip() == fact.value:
                    fact.memory_category = str(result.get("memory_category", "custom")).strip() or "custom"
                    fact.validated = True
                    conflict_ids = result.get("conflict_ids", [])
                    if isinstance(conflict_ids, list):
                        fact.conflict_with = [str(cid) for cid in conflict_ids]
                    break

        # 删除重复的记忆
        entry.runtime.memory_facts = [
            fact for i, fact in enumerate(entry.runtime.memory_facts)
            if i not in duplicate_indices
        ]

        self._record_task_stat(transcript, task_name, "succeeded")

    def _has_multimodal_inputs(self, transcript: Transcript) -> bool:
        """检测 transcript 中最后的用户消息是否包含多模态输入。
        
        Returns:
            True 如果最后的用户消息有多模态输入，否则 False
        """
        # 从后往前遍历，找到最后一条用户消息
        for message in reversed(transcript.messages):
            if message.role == "user":
                # 检查是否有多模态输入
                return bool(message.multimodal_inputs)
        return False

    # ── Engagement Decision System (v0.14.0) ──

    def _build_heat_analysis(
        self,
        *,
        transcript: Transcript,
        config: SessionConfig,
        group_recent_count: int,
    ) -> HeatAnalysis:
        """构建热度分析所需的数据并执行分析。"""
        window = float(config.orchestration.heat_window_seconds)

        # 收集窗口内活跃参与者
        active_ids: set[str] = set()
        assistant_count = 0
        for msg in transcript.messages[-(group_recent_count + 10):]:
            if msg.role == "assistant":
                assistant_count += 1
            if msg.role == "user" and msg.speaker:
                active_ids.add(msg.speaker)

        return HeatAnalyzer.analyze(
            group_recent_count=group_recent_count,
            window_seconds=window,
            active_participant_ids=active_ids,
            assistant_reply_count_in_window=min(
                assistant_count,
                len(transcript.reply_runtime.assistant_reply_timestamps),
            ),
        )

    async def _run_engagement_intent_analysis(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        content: str,
    ) -> IntentAnalysis:
        """执行新版意图分析（携带参与者上下文）。"""
        agent_alias = str(config.agent.metadata.get("alias", "")).strip()

        # 从最近消息中提取参与者名称（排除 AI 自身）
        participant_names: list[str] = []
        seen: set[str] = set()
        for msg in reversed(transcript.messages[-20:]):
            if msg.role == "user" and msg.speaker and msg.speaker not in seen:
                seen.add(msg.speaker)
                participant_names.append(msg.speaker)

        if not config.orchestration.enable_intent_analysis:
            return IntentAnalyzer.fallback_analysis(
                content, config.agent.name, agent_alias, participant_names,
            )

        model = config.orchestration.intent_analysis_model.strip()
        if not model:
            try:
                model = self.get_model_for_task(config, self._TASK_INTENT_ANALYSIS)
            except ValueError:
                model = config.agent.model

        agent_alias = str(config.agent.metadata.get("alias", "")).strip()

        # 近期上下文（含 speaker）
        recent_messages: list[dict[str, str]] = []
        for msg in transcript.messages[-8:]:
            if msg.role in ("user", "assistant"):
                entry: dict[str, str] = {"role": msg.role, "content": msg.content}
                if msg.speaker:
                    entry["speaker"] = msg.speaker
                recent_messages.append(entry)

        return await IntentAnalyzer.analyze(
            content=content,
            agent_name=config.agent.name,
            agent_alias=agent_alias,
            participant_names=participant_names,
            recent_messages=recent_messages,
            call_provider=self._call_provider,
            model=model,
        )

    @staticmethod
    def _should_reply_for_turn(turn: Message) -> bool:
        """Check reply_mode: never → False, otherwise → True.

        For auto/smart modes the actual decision is made later by
        the engagement system inside ``_process_live_turn``.
        """
        mode = str(getattr(turn, "reply_mode", "always") or "always").strip().lower()
        return mode not in {"never", "silent", "none", "no_reply"}

    def _get_model_for_chat(self, config: SessionConfig, transcript: Transcript) -> str:
        """根据是否有多模态输入，动态选择主模型。
        
        策略：
        - 如果最后用户消息有多模态输入，使用 multimodal_model（如果配置）
        - 否则使用默认的 agent.model
        
        Args:
            config: 会话配置
            transcript: 当前会话 transcript
            
        Returns:
            选定的模型名称
        """
        # 检查是否有多模态输入
        if self._has_multimodal_inputs(transcript):
            # 尝试从 agent.metadata 中获取多模态模型
            multimodal_model = config.agent.metadata.get("multimodal_model", "")
            if multimodal_model:
                return multimodal_model
        
        # 默认返回配置的主模型
        return config.agent.model

    @classmethod
    def _is_internal_memory_metadata_line(cls, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False

        for pattern in cls._MEMORY_METADATA_LINE_PATTERNS:
            if pattern.match(stripped):
                return True

        if "|" not in stripped:
            return False

        cn_hits = sum(1 for p in cls._MEMORY_METADATA_CN_LABEL_PATTERNS if p.search(stripped))
        en_hits = sum(1 for p in cls._MEMORY_METADATA_EN_LABEL_PATTERNS if p.search(stripped))
        return cn_hits >= 2 or en_hits >= 2

    def _sanitize_assistant_content(self, content: str) -> str:
        if not content:
            return content

        cleaned_lines: list[str] = []
        for line in content.splitlines():
            if self._is_internal_memory_metadata_line(line):
                continue
            cleaned_lines.append(line)

        cleaned = "\n".join(cleaned_lines).strip()
        if cleaned:
            return cleaned
        return "收到。"

    @staticmethod
    def _collect_internal_system_notes(transcript: Transcript) -> str:
        notes: list[str] = []
        for message in transcript.messages:
            if message.role != "system":
                continue
            text = message.content.strip()
            if text:
                notes.append(text)
        if not notes:
            return ""
        return "\n".join(notes)

    def _build_chat_main_request_context(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        skill_descriptions: str = "",
        environment_context: str = "",
        skip_sections: list[str] | None = None,
        self_memory: SelfMemoryManager | None = None,
    ) -> tuple[str, list[dict[str, object]]]:
        # Build self-memory prompt sections
        diary_section = ""
        glossary_section = ""
        if self_memory is not None and config.orchestration.enable_self_memory:
            # Extract keywords from recent messages for relevance
            recent_keywords: list[str] = []
            for msg in transcript.messages[-6:]:
                if msg.content.strip():
                    recent_keywords.extend(msg.content[:100].split())
            diary_section = self_memory.build_diary_prompt_section(
                keywords=recent_keywords,
                max_entries=config.orchestration.self_memory_max_diary_prompt_entries,
            )
            # Build glossary from recent conversation content
            recent_text = " ".join(
                msg.content[:200] for msg in transcript.messages[-6:] if msg.content.strip()
            )
            glossary_section = self_memory.build_glossary_prompt_section(
                text=recent_text,
                max_terms=config.orchestration.self_memory_max_glossary_prompt_terms,
            )

        system_prompt = self._build_system_prompt(
            config, transcript,
            skill_descriptions=skill_descriptions,
            environment_context=environment_context,
            skip_sections=skip_sections or [],
            diary_section=diary_section,
            glossary_section=glossary_section,
        )
        internal_notes = self._collect_internal_system_notes(transcript)
        if internal_notes:
            system_prompt = (
                f"{system_prompt}\n\n"
                "[会话内部系统补充]\n"
                "以下为引擎内部记录的系统上下文，用于辅助推理；"
                "请勿在最终回复中逐字复述。\n"
                f"{internal_notes}"
            )

        chat_history: list[dict[str, object]] = []
        for message in transcript.messages:
            role = str(message.role or "").strip().lower()
            if role == "system":
                continue
            speaker_prefix = f"[{message.speaker}] " if message.speaker else ""
            text_content = f"{speaker_prefix}{message.content}"
            image_inputs = [
                item for item in message.multimodal_inputs
                if item.get("type") == "image" and item.get("value")
            ]
            if image_inputs and role == "user":
                content_parts: list[dict[str, object]] = [{"type": "text", "text": text_content}]
                for image in image_inputs:
                    content_parts.append(
                        {"type": "image_url", "image_url": {"url": image["value"]}}
                    )
                chat_history.append({"role": message.role, "content": content_parts})
            else:
                chat_history.append({"role": message.role, "content": text_content})
        return system_prompt, chat_history

    @staticmethod
    def _build_memory_extract_task_input(
        *,
        transcript: Transcript,
        participant: Participant,
        content: str,
        max_context_messages: int = 8,
        max_context_chars: int = 1200,
    ) -> str:
        context_lines: list[str] = []
        for message in transcript.messages:
            role = str(message.role or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = str(message.content or "").strip()
            if not text:
                continue
            speaker = str(message.speaker or role).strip()
            context_lines.append(f"[{role}][{speaker}] {text}")

        if max_context_messages > 0:
            context_lines = context_lines[-max_context_messages:]

        context_text = "\n".join(context_lines)
        if max_context_chars > 0 and len(context_text) > max_context_chars:
            context_text = context_text[-max_context_chars:]

        return (
            f"user_id={participant.user_id}\n"
            f"speaker={participant.name}\n"
            f"latest_user_content={content}\n"
            "conversation_context=\n"
            f"{context_text}"
        )

    async def _generate_assistant_message(
        self,
        config: SessionConfig,
        transcript: Transcript,
        skill_registry: SkillRegistry | None = None,
        skill_executor: SkillExecutor | None = None,
        environment_context: str = "",
        event_bus: SessionEventBus | None = None,
        skip_sections: list[str] | None = None,
        self_memory: SelfMemoryManager | None = None,
    ) -> Message:
        if config.enable_auto_compression:
            transcript.compress_for_budget(
                max_messages=config.history_max_messages,
                max_chars=config.history_max_chars,
            )
        
        # Build skill descriptions if available
        skill_descriptions = ""
        if skill_registry is not None:
            skill_descriptions = skill_registry.build_tool_descriptions()

        # 动态选择模型：有多模态输入时自动升级到多模态模型
        model = self._get_model_for_chat(config, transcript)
        system_prompt, chat_history = self._build_chat_main_request_context(
            config=config,
            transcript=transcript,
            skill_descriptions=skill_descriptions,
            environment_context=environment_context,
            skip_sections=skip_sections,
            self_memory=self_memory,
        )
        
        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=chat_history,
            temperature=config.agent.temperature,
            max_tokens=config.agent.max_tokens,
            purpose="chat_main",
        )
        retry_times = int(config.orchestration.task_retries.get("chat_main", 0))
        content = await self._call_provider_with_retry(
            request_payload=request_payload,
            retry_times=retry_times,
            transcript=transcript,
            task_name="chat_main",
            actor_id=config.agent.name,
        )
        
        # 清理：移除模型响应中可能的 speaker 前缀（防止重复前缀化）
        # 匹配 "[任意名字] " 开头的格式，统一移除
        speaker = str(config.agent.metadata.get("alias", "")).strip() or config.agent.name
        if content.startswith("["):
            bracket_end = content.find("] ", 1)
            if bracket_end != -1 and bracket_end < 40:
                content = content[bracket_end + 2:]

        content = self._sanitize_assistant_content(content)

        # Diagnose SKILL_CALL output even when skill runtime is not ready.
        initial_skill_calls = parse_skill_calls(content)
        if initial_skill_calls and (skill_registry is None or skill_executor is None):
            logger.warning(
                "检测到SKILL_CALL，但技能运行时未就绪；将跳过执行。calls=%s",
                [name for name, _ in initial_skill_calls],
            )

        # --- Skill call detection and execution ---
        skill_executed = False
        last_skill_name = ""
        last_skill_result_text = ""
        first_partial_content = ""
        if skill_registry is not None and skill_executor is not None:
            max_rounds = max(1, config.orchestration.max_skill_rounds)
            # SkillChainContext spans all rounds in this turn for result tracking/logging.
            chain_ctx = SkillChainContext()
            for _round in range(max_rounds):
                calls = parse_skill_calls(content)
                if not calls:
                    break

                # Iterative feedback loop: process ONE skill per round.
                # Its result is injected into the transcript before re-generating, so the model
                # reads the actual output and decides the next step (another SKILL or final answer).
                skill_name, skill_params = calls[0]
                had_unknown_skill = False

                skill_def = skill_registry.get(skill_name)
                if skill_def is None:
                    # Skill registry can be stale — try reloading once.
                    try:
                        reloaded = skill_registry.load_from_directory(
                            config.work_path / "skills",
                            auto_install_deps=config.orchestration.auto_install_skill_deps,
                        )
                        if reloaded > 0:
                            logger.info(
                                "检测到SKILL_CALL未命中，已重载技能目录后重试: loaded=%d | skill=%s",
                                reloaded,
                                skill_name,
                            )
                    except Exception:
                        logger.warning(
                            "重载SKILL目录失败，继续按未知SKILL处理: skill=%s",
                            skill_name,
                            exc_info=True,
                        )
                    skill_def = skill_registry.get(skill_name)

                if skill_def is None:
                    # Unknown skill — inject error; model will recover on re-generation.
                    transcript.add(Message(
                        role="system",
                        content=f"[SKILL系统] 未找到名为 '{skill_name}' 的SKILL，请使用可用SKILL列表中的名称。",
                    ))
                    if event_bus is not None:
                        await event_bus.emit(SessionEvent(
                            type=SessionEventType.SKILL_COMPLETED,
                            data={
                                "skill_name": skill_name,
                                "success": False,
                                "result_preview": "SKILL未找到",
                            },
                        ))
                    had_unknown_skill = True
                else:
                    logger.info(
                        "执行SKILL: %s | 参数: %s | 迭代轮次: %d/%d",
                        skill_name, skill_params, _round + 1, max_rounds,
                    )
                    if event_bus is not None:
                        await event_bus.emit(SessionEvent(
                            type=SessionEventType.SKILL_STARTED,
                            data={"skill_name": skill_name, "params": skill_params},
                        ))
                    skill_result = await skill_executor.execute_async(
                        skill_def,
                        skill_params,
                        timeout=float(config.orchestration.skill_execution_timeout),
                    )
                    chain_ctx.store(skill_name, skill_result)
                    skill_executed = True
                    last_skill_name = skill_name
                    result_text = skill_result.to_display_text()
                    last_skill_result_text = result_text
                    if event_bus is not None:
                        await event_bus.emit(SessionEvent(
                            type=SessionEventType.SKILL_COMPLETED,
                            data={
                                "skill_name": skill_name,
                                "success": skill_result.success,
                                "result_preview": result_text[:200],
                            },
                        ))

                    # Inject result as system message; model reads it on the next generation.
                    transcript.add(Message(
                        role="system",
                        content=f"[SKILL执行结果: {skill_name}]\n{result_text}",
                    ))

                # Emit any non-SKILL text from this round as an intermediate partial message.
                # Skip if the skill was unknown — prefer clean regeneration in that case.
                remaining_content = strip_skill_calls(content)
                if remaining_content.strip() and not had_unknown_skill:
                    _split_marker = (
                        config.orchestration.split_marker
                        if config.orchestration.enable_prompt_driven_splitting
                        else None
                    )
                    _partial_parts = (
                        remaining_content.split(_split_marker)
                        if _split_marker and _split_marker in remaining_content
                        else [remaining_content]
                    )
                    for _partial_part in _partial_parts:
                        _partial_stripped = _partial_part.strip()
                        if not _partial_stripped:
                            continue
                        partial_msg = Message(
                            role="assistant",
                            content=_partial_stripped.rstrip(),
                            speaker=speaker,
                        )
                        transcript.add(partial_msg)
                        if not first_partial_content:
                            first_partial_content = partial_msg.content
                        if event_bus is not None:
                            await event_bus.emit(SessionEvent(
                                type=SessionEventType.MESSAGE_ADDED,
                                message=partial_msg,
                                data={"intermediate": True, "source": "skill_partial"},
                            ))
                        await asyncio.sleep(0.01)

                # Re-generate: model sees the injected result and decides next step.
                if config.enable_auto_compression:
                    transcript.compress_for_budget(
                        max_messages=config.history_max_messages,
                        max_chars=config.history_max_chars,
                    )
                system_prompt, chat_history = self._build_chat_main_request_context(
                    config=config,
                    transcript=transcript,
                    skill_descriptions=skill_descriptions,
                    environment_context=environment_context,
                    skip_sections=skip_sections,
                    self_memory=self_memory,
                )
                request_payload = GenerationRequest(
                    model=model,
                    system_prompt=system_prompt,
                    messages=chat_history,
                    temperature=config.agent.temperature,
                    max_tokens=config.agent.max_tokens,
                    purpose="chat_main",
                )
                content = await self._call_provider_with_retry(
                    request_payload=request_payload,
                    retry_times=retry_times,
                    transcript=transcript,
                    task_name="chat_main",
                    actor_id=config.agent.name,
                )
                if content.startswith("["):
                    bracket_end = content.find("] ", 1)
                    if bracket_end != -1 and bracket_end < 40:
                        content = content[bracket_end + 2:]
                content = self._sanitize_assistant_content(content)

        # If SKILL was executed but final content is empty (or only markers),
        # force one extra generation round that must produce a direct answer.
        if skill_executed and not strip_skill_calls(content).strip():
            transcript.add(Message(
                role="system",
                content=(
                    "[SKILL系统] 你已经拿到技能执行结果。"
                    "请直接给用户最终答复，不要再次调用SKILL。"
                ),
            ))
            if config.enable_auto_compression:
                transcript.compress_for_budget(
                    max_messages=config.history_max_messages,
                    max_chars=config.history_max_chars,
                )
            system_prompt, chat_history = self._build_chat_main_request_context(
                config=config,
                transcript=transcript,
                skill_descriptions=skill_descriptions,
                environment_context=environment_context,
                skip_sections=skip_sections,
                self_memory=self_memory,
            )
            request_payload = GenerationRequest(
                model=model,
                system_prompt=system_prompt,
                messages=chat_history,
                temperature=config.agent.temperature,
                max_tokens=config.agent.max_tokens,
                purpose="chat_main",
            )
            content = await self._call_provider_with_retry(
                request_payload=request_payload,
                retry_times=retry_times,
                transcript=transcript,
                task_name="chat_main",
                actor_id=config.agent.name,
            )
            if content.startswith("["):
                bracket_end = content.find("] ", 1)
                if bracket_end != -1 and bracket_end < 40:
                    content = content[bracket_end + 2:]
            content = self._sanitize_assistant_content(content)

        # Safety: strip any SKILL_CALL markers left in content (e.g. max_skill_rounds exhausted)
        content = strip_skill_calls(content)
        if not content.strip() and skill_executed:
            summary_lines: list[str] = []
            if first_partial_content.strip():
                summary_lines.append(first_partial_content.strip())
            if last_skill_result_text.strip():
                compact = " ".join(last_skill_result_text.split())
                summary_lines.append(
                    f"{last_skill_name or 'SKILL'} 已执行，结果摘要：{compact[:220]}"
                )
            if summary_lines:
                content = "\n".join(summary_lines)
            else:
                content = f"已执行 {last_skill_name or 'SKILL'}，但暂未生成可用回复，请稍后重试。"

        last_message: Message | None = None

        if config.orchestration.enable_prompt_driven_splitting:
            marker = config.orchestration.split_marker
            # 仅在 marker 精确出现时才分割，避免其他 [...] 模式的误判
            if marker in content:
                # 识别到分割标记，拆分消息
                parts = content.split(marker)
                for part in parts:
                    # 每个分割段也移除可能的 speaker 前缀
                    part_stripped = part.strip()
                    if part_stripped.startswith("["):
                        pb_end = part_stripped.find("] ", 1)
                        if pb_end != -1 and pb_end < 40:
                            part_stripped = part_stripped[pb_end + 2:].strip()
                    if part_stripped:  # 跳过空白部分
                        msg = Message(
                            role="assistant",
                            content=part_stripped.rstrip(),
                            speaker=speaker,
                        )
                        transcript.add(msg)
                        last_message = msg
                        if event_bus is not None:
                            await event_bus.emit(SessionEvent(
                                type=SessionEventType.MESSAGE_ADDED,
                                message=msg,
                            ))
                        # 在消息之间增加小延迟，模拟实时聊天
                        if part != parts[-1]:  # 不是最后一条
                            await asyncio.sleep(0.01)
        
        # 如果没有分割标记，或未启用分割，则按常规处理
        if last_message is None:
            assistant_message = Message(
                role="assistant",
                content=content.rstrip(),
                speaker=speaker,
            )
            transcript.add(assistant_message)
            if event_bus is not None:
                await event_bus.emit(SessionEvent(
                    type=SessionEventType.MESSAGE_ADDED,
                    message=assistant_message,
                ))
            return assistant_message
        
        return last_message

    async def _add_human_turn(
        self,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        task_token_usage: dict[str, int],
        event_store: EventMemoryManager,
        known_entities: list[str],
        channel: str | None = None,
        channel_user_id: str | None = None,
        multimodal_inputs: list[dict[str, str]] | None = None,
        user_message_count_since_extract: dict[str, int] | None = None,
    ) -> dict[str, object]:
        normalized_multimodal_inputs = self._normalize_multimodal_inputs(
            multimodal_inputs or [],
            max_items=max(1, int(config.orchestration.max_multimodal_inputs_per_turn)),
            max_value_length=max(1, int(config.orchestration.max_multimodal_value_length)),
        )
        transcript.add(
            Message(
                role="user",
                content=content,
                speaker=participant.name,
                channel=channel,
                channel_user_id=channel_user_id,
                multimodal_inputs=normalized_multimodal_inputs,
            )
        )
        transcript.remember_participant(
            participant=participant,
            content=content,
            max_recent_messages=config.max_recent_participant_messages,
            channel=channel,
            channel_user_id=channel_user_id,
        )
        
        # ============================================================================
        # Memory Extract 频率控制 - 避免调用过于频繁导致内容碎片化
        # ============================================================================
        batch_size = int(getattr(config.orchestration, 'memory_extract_batch_size', 1))
        min_length = int(getattr(config.orchestration, 'memory_extract_min_content_length', 0))
        user_message_count_since_extract = user_message_count_since_extract or {}
        current_count = user_message_count_since_extract.get(participant.user_id, 0)
        
        should_run_memory_extract = (
            # 条件1：消息计数达到批处理大小
            (current_count + 1) % batch_size == 0
            # 条件2：内容长度满足最小要求
            and len(content) >= min_length
        )

        if should_run_memory_extract:
            user_message_count_since_extract[participant.user_id] = 0
        else:
            user_message_count_since_extract[participant.user_id] = current_count + 1

        # ============================================================================
        # 事件系统 v2：缓冲消息 + 批量提取观察
        # ============================================================================
        event_enabled = config.orchestration.task_enabled.get(self._TASK_EVENT_EXTRACT, True)
        if event_enabled:
            event_store.buffer_message(user_id=participant.user_id, content=content)

        event_batch_size = int(getattr(config.orchestration, 'event_extract_batch_size', 5))
        should_run_event_extract = (
            event_enabled
            and event_store.should_extract(participant.user_id, batch_size=event_batch_size)
        )

        # 并行启动可并行的任务
        memory_extract_task: asyncio.Task[None] | None = None
        if should_run_memory_extract:
            memory_extract_task = asyncio.create_task(
                self._run_memory_extract_task(
                    config=config,
                    transcript=transcript,
                    participant=participant,
                    content=content,
                    task_token_usage=task_token_usage,
                )
            )

        event_extract_task: asyncio.Task[list[object]] | None = None
        if should_run_event_extract:
            event_extract_task = asyncio.create_task(
                self._run_batch_event_extract(
                    config=config,
                    transcript=transcript,
                    participant=participant,
                    task_token_usage=task_token_usage,
                    event_store=event_store,
                )
            )

        pending_tasks: list[asyncio.Task[object]] = []
        if memory_extract_task is not None:
            pending_tasks.append(cast(asyncio.Task[object], memory_extract_task))
        if event_extract_task is not None:
            pending_tasks.append(cast(asyncio.Task[object], event_extract_task))
        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        # 获取事件相关度（用于回复意愿评估），不需要 LLM 调用
        hit_payload = event_store.check_relevance(
            user_id=participant.user_id, content=content,
        )

        # 运行 memory_manager 任务汇聚、去重、标注、验证记忆
        await self._run_memory_manager_task(
            config=config,
            transcript=transcript,
            participant=participant,
            task_token_usage=task_token_usage,
        )
        return hit_payload

    async def run_session(
        self,
        config: SessionConfig,
        transcript: Transcript | None = None,
    ) -> Transcript:
        # 验证多模型协同配置
        self.validate_orchestration_config(config)
        return self._prepare_transcript(config, transcript)

    async def run_live_session(
        self,
        config: SessionConfig,
        transcript: Transcript | None = None,
    ) -> Transcript:
        """Initialize a live session and prepare runtime context.

        Breaking change: this method no longer processes user messages.
        Use run_live_message(...) for per-message input/output handling.
        """
        self.validate_orchestration_config(config)

        transcript = self._prepare_transcript(config, transcript)
        context = self._get_or_create_live_context(config=config, transcript=transcript)

        # Start background consolidation if configured
        if context.bg_task_manager is not None and not context.bg_task_manager.is_running():
            await context.bg_task_manager.start()

        return transcript

    async def subscribe(
        self,
        transcript: Transcript,
        *,
        max_queue_size: int = 256,
    ) -> AsyncIterator[SessionEvent]:
        """Subscribe to real-time session events for the given transcript.

        Returns an async iterator that yields :class:`SessionEvent` objects
        as they are produced by the engine (new messages, SKILL status,
        processing lifecycle, etc.).

        The iterator terminates when the session's event bus is closed.

        Args:
            transcript: The transcript (session) to subscribe to.
            max_queue_size: Maximum buffered events per subscriber.

        Yields:
            SessionEvent instances in chronological order.
        """
        key = id(transcript)
        context = self._live_session_contexts.get(key)
        if context is None:
            raise ValueError(
                "未找到与此 transcript 关联的活跃会话。"
                "请先调用 run_live_session() 初始化会话。"
            )
        async for event in context.event_bus.subscribe(max_queue_size=max_queue_size):
            yield event

    async def run_live_message(
        self,
        config: SessionConfig,
        turn: Message,
        transcript: Transcript | None = None,
        session_reply_mode: str | None = None,
        finalize_and_persist: bool = True,
        environment_context: str = "",
        user_profile: UserProfile | None = None,
        on_reply: Callable[[Message], Awaitable[None]] | None = None,
        timeout: float = 0,
    ) -> Transcript:
        self.validate_orchestration_config(config)

        transcript = self._prepare_transcript(config, transcript)
        if turn.role != "user" or not turn.speaker:
            raise ValueError("run_live_message 仅接受带 speaker 的单条 user 消息。")

        # Auto-register user profile if provided
        if user_profile is not None:
            transcript.user_memory.register_user(user_profile)

        if on_reply is not None:
            return await self._run_live_message_with_callback(
                config=config,
                turn=turn,
                transcript=transcript,
                session_reply_mode=session_reply_mode,
                finalize_and_persist=finalize_and_persist,
                environment_context=environment_context,
                on_reply=on_reply,
                timeout=timeout,
            )

        if timeout > 0:
            return await asyncio.wait_for(
                self._run_live_message_core(
                    config=config,
                    turn=turn,
                    transcript=transcript,
                    session_reply_mode=session_reply_mode,
                    finalize_and_persist=finalize_and_persist,
                    environment_context=environment_context,
                ),
                timeout=timeout,
            )

        return await self._run_live_message_core(
            config=config,
            turn=turn,
            transcript=transcript,
            session_reply_mode=session_reply_mode,
            finalize_and_persist=finalize_and_persist,
            environment_context=environment_context,
        )

    async def _run_live_message_with_callback(
        self,
        config: SessionConfig,
        turn: Message,
        transcript: Transcript,
        session_reply_mode: str | None,
        finalize_and_persist: bool,
        environment_context: str,
        on_reply: Callable[[Message], Awaitable[None]],
        timeout: float,
    ) -> Transcript:
        """Process a message while delivering assistant replies via *on_reply*.

        Internally subscribes to the session event bus and calls *on_reply*
        for every assistant ``MESSAGE_ADDED`` event.  The subscription and
        tear-down are fully managed so that callers don't need to deal with
        ``asyncio.create_task`` / ``asubscribe`` boilerplate.
        """

        context = self._get_or_create_live_context(config=config, transcript=transcript)

        async def _consume_events() -> None:
            try:
                async for evt in context.event_bus.subscribe():
                    if evt.type in (
                        SessionEventType.PROCESSING_COMPLETED,
                        SessionEventType.REPLY_SKIPPED,
                        SessionEventType.ERROR,
                    ):
                        break
                    if evt.type == SessionEventType.MESSAGE_ADDED:
                        msg = evt.message
                        if msg is not None and msg.role == "assistant":
                            content = (msg.content or "").strip()
                            if content:
                                try:
                                    await on_reply(msg)
                                except Exception:
                                    logger.error(
                                        "on_reply callback error", exc_info=True,
                                    )
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.error("Event consume error in on_reply path", exc_info=True)

        consume_task = asyncio.create_task(_consume_events())
        # Wait until subscription is registered to avoid missing early events.
        for _ in range(100):
            if consume_task.done() or context.event_bus.subscriber_count > 0:
                break
            await asyncio.sleep(0)
        if context.event_bus.subscriber_count == 0:
            logger.warning("on_reply consumer was not subscribed before processing")

        try:
            core_coro = self._run_live_message_core(
                config=config,
                turn=turn,
                transcript=transcript,
                session_reply_mode=session_reply_mode,
                finalize_and_persist=finalize_and_persist,
                environment_context=environment_context,
            )
            if timeout > 0:
                transcript = await asyncio.wait_for(core_coro, timeout=timeout)
            else:
                transcript = await core_coro
        except asyncio.TimeoutError:
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass
            raise

        # Wait for the consumer to finish flushing the last event(s).
        try:
            await asyncio.wait_for(consume_task, timeout=120.0)
        except asyncio.TimeoutError:
            logger.warning("on_reply consumer timed out; cancelling")
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass

        return transcript

    async def _run_live_message_core(
        self,
        config: SessionConfig,
        turn: Message,
        transcript: Transcript,
        session_reply_mode: str | None,
        finalize_and_persist: bool,
        environment_context: str,
    ) -> Transcript:

        context = self._get_or_create_live_context(config=config, transcript=transcript)
        participant = self._resolve_participant_for_turn(
            transcript=transcript,
            turn=turn,
            context=context,
        )

        debounce_seconds = float(config.orchestration.message_debounce_seconds)
        if debounce_seconds > 0:
            pending = context.pending_turn
            if pending is not None and pending.participant_user_id == participant.user_id:
                # Same user — accumulate into buffer, then let the last coroutine flush.
                # Each coroutine sleeps the full debounce window; only the one whose
                # `turn` is still at pending.messages[-1] when the sleep ends will merge
                # all accumulated messages into a single call to _process_live_turn,
                # ensuring intent analysis and profile extraction fire exactly once.
                pending.messages.append(turn)
                # Wait for debounce window.  CancelledError must propagate
                # so that external timeout (asyncio.wait_for) works correctly.
                await asyncio.sleep(debounce_seconds)
                # After sleep, check if we are still the latest (no newer message appended)
                if context.pending_turn is pending and pending.messages[-1] is turn:
                    # Timer expired and we are still the latest → flush all as one message
                    merged_turn = self._merge_pending_turns(pending.messages)
                    context.pending_turn = None
                    return await self._process_live_turn(
                        config=config,
                        turn=merged_turn,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=finalize_and_persist,
                        context=context,
                        participant=participant,
                        environment_context=environment_context,
                    )
                return transcript
            else:
                # Different user or first message — flush pending if any, then buffer new
                if pending is not None and pending.messages:
                    old_participant = self._resolve_participant_for_turn(
                        transcript=transcript,
                        turn=pending.messages[0],
                        context=context,
                    )
                    merged_old = self._merge_pending_turns(pending.messages)
                    context.pending_turn = None
                    await self._process_live_turn(
                        config=config,
                        turn=merged_old,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=False,
                        context=context,
                        participant=old_participant,
                        environment_context=environment_context,
                    )
                # Buffer new turn
                context.pending_turn = _PendingTurn(
                    participant_user_id=participant.user_id,
                    messages=[turn],
                )
                # Wait for debounce window.  CancelledError must propagate
                # so that external timeout (asyncio.wait_for) works correctly.
                await asyncio.sleep(debounce_seconds)
                # After sleep, check if still the latest
                if context.pending_turn is not None and context.pending_turn.participant_user_id == participant.user_id and context.pending_turn.messages[-1] is turn:
                    merged_turn = self._merge_pending_turns(context.pending_turn.messages)
                    context.pending_turn = None
                    return await self._process_live_turn(
                        config=config,
                        turn=merged_turn,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=finalize_and_persist,
                        context=context,
                        participant=participant,
                        environment_context=environment_context,
                    )
                return transcript

        # No debounce — process immediately
        return await self._process_live_turn(
            config=config,
            turn=turn,
            transcript=transcript,
            session_reply_mode=session_reply_mode,
            finalize_and_persist=finalize_and_persist,
            context=context,
            participant=participant,
            environment_context=environment_context,
        )

    @staticmethod
    def _merge_pending_turns(messages: list[Message]) -> Message:
        """Merge multiple buffered messages from the same user into one."""
        if len(messages) == 1:
            return messages[0]
        merged_content = "\n".join(m.content for m in messages if m.content.strip())
        # Collect all multimodal inputs
        merged_multimodal: list[dict[str, str]] = []
        for m in messages:
            merged_multimodal.extend(m.multimodal_inputs)
        first = messages[0]
        return Message(
            role=first.role,
            content=merged_content,
            speaker=first.speaker,
            channel=first.channel,
            channel_user_id=first.channel_user_id,
            multimodal_inputs=merged_multimodal,
            reply_mode=first.reply_mode,
        )

    async def _process_live_turn(
        self,
        config: SessionConfig,
        turn: Message,
        transcript: Transcript,
        session_reply_mode: str | None,
        finalize_and_persist: bool,
        context: LiveSessionContext,
        participant: Participant,
        environment_context: str = "",
    ) -> Transcript:
        """Core processing logic for a single user turn (after debounce)."""
        self._ensure_skill_runtime(config=config, context=context)

        known_entities = self._build_known_entities(context.known_by_id)

        user_last_turn_at: dict[str, datetime] = {}
        for user_id, raw_time in transcript.reply_runtime.user_last_turn_at.items():
            parsed = self._parse_runtime_datetime(str(raw_time))
            if parsed is not None:
                user_last_turn_at[user_id] = parsed

        group_recent_turns: list[datetime] = []
        for raw_time in transcript.reply_runtime.group_recent_turn_timestamps:
            parsed = self._parse_runtime_datetime(str(raw_time))
            if parsed is not None:
                group_recent_turns.append(parsed)

        last_assistant_reply_at = self._parse_runtime_datetime(
            transcript.reply_runtime.last_assistant_reply_at
        )

        now = datetime.now(timezone.utc)
        previous_user_turn_at = user_last_turn_at.get(participant.user_id)
        user_interval_seconds: float | None = None
        if previous_user_turn_at is not None:
            user_interval_seconds = max(0.0, (now - previous_user_turn_at).total_seconds())
        user_last_turn_at[participant.user_id] = now

        group_recent_turns.append(now)
        group_window_start = now.timestamp() - float(config.orchestration.heat_window_seconds)
        group_recent_turns = [item for item in group_recent_turns if item.timestamp() >= group_window_start]
        group_recent_count = len(group_recent_turns)

        event_hit_payload = await self._add_human_turn(
            config,
            transcript,
            participant,
            turn.content,
            task_token_usage=context.task_token_usage,
            event_store=context.event_store,
            known_entities=known_entities,
            channel=turn.channel,
            channel_user_id=turn.channel_user_id,
            multimodal_inputs=turn.multimodal_inputs,
            user_message_count_since_extract=context.user_message_count_since_extract,
        )

        assistant_interval_seconds: float | None = None
        if last_assistant_reply_at is not None:
            assistant_interval_seconds = max(
                0.0,
                (now - last_assistant_reply_at).total_seconds(),
            )

        resolved_session_mode = (
            str(session_reply_mode).strip().lower()
            if session_reply_mode is not None and str(session_reply_mode).strip()
            else self._resolve_session_reply_mode(config)
        )
        effective_turn = Message(
            role=turn.role,
            content=turn.content,
            speaker=turn.speaker,
            channel=turn.channel,
            channel_user_id=turn.channel_user_id,
            multimodal_inputs=list(turn.multimodal_inputs),
            reply_mode=resolved_session_mode,
        )
        should_reply = self._should_reply_for_turn(effective_turn)

        # ── Engagement System: heat + intent → decision ──
        intent: IntentAnalysis | None = None
        engagement: EngagementDecision | None = None
        if should_reply and resolved_session_mode in ("auto", "smart"):
            heat = self._build_heat_analysis(
                transcript=transcript,
                config=config,
                group_recent_count=group_recent_count,
            )
            intent = await self._run_engagement_intent_analysis(
                config=config,
                transcript=transcript,
                content=turn.content,
            )
            engagement = EngagementCoordinator.decide(
                heat=heat,
                intent=intent,
                sensitivity=float(config.orchestration.engagement_sensitivity),
            )
            should_reply = engagement.should_reply
            logger.info(
                "[Engagement] speaker=%s | score=%.3f | heat=%s(%.2f) | "
                "target=%s | reason=%s",
                turn.speaker,
                engagement.engagement_score,
                engagement.heat.heat_level if engagement.heat else "N/A",
                engagement.heat.heat_score if engagement.heat else 0.0,
                engagement.intent.target if engagement.intent else "N/A",
                engagement.reason,
            )

        # Emit user message event
        await context.event_bus.emit(SessionEvent(
            type=SessionEventType.MESSAGE_ADDED,
            message=turn,
            data={"participant_user_id": participant.user_id},
        ))

        # ── Reply frequency limiter ──
        is_mentioned = (
            intent is not None and intent.directed_at_ai
        ) if intent else False
        if should_reply and EngagementCoordinator.check_reply_frequency_limit(
            assistant_reply_timestamps=list(
                transcript.reply_runtime.assistant_reply_timestamps
            ),
            now=now,
            window_seconds=float(config.orchestration.reply_frequency_window_seconds),
            max_replies=int(config.orchestration.reply_frequency_max_replies),
            exempt_on_mention=bool(config.orchestration.reply_frequency_exempt_on_mention),
            is_mentioned=is_mentioned,
        ):
            should_reply = False
            await context.event_bus.emit(SessionEvent(
                type=SessionEventType.REPLY_SKIPPED,
                data={"speaker": turn.speaker, "reason": "frequency_limit"},
            ))

        if should_reply:
            logger.info(
                "[会话] 触发回复 | speaker=%s | session_reply_mode=%s",
                turn.speaker,
                resolved_session_mode,
            )

            await context.event_bus.emit(SessionEvent(
                type=SessionEventType.PROCESSING_STARTED,
                data={"speaker": turn.speaker},
            ))

            # Acquire LLM concurrency semaphore before main generation (if configured).
            # Algorithm-based steps (heat, intent keyword path) have already run without
            # the semaphore so they are unaffected by queuing here.
            _sem = context.llm_semaphore
            async with (_sem if _sem is not None else _noop_semaphore()):
                assistant_message = await self._generate_assistant_message(
                    config,
                    transcript,
                    skill_registry=context.skill_registry,
                    skill_executor=context.skill_executor,
                    environment_context=environment_context,
                    event_bus=context.event_bus,
                    skip_sections=intent.skip_sections if intent else [],
                    self_memory=context.self_memory if config.orchestration.enable_self_memory else None,
                )
            last_assistant_reply_at = datetime.now(timezone.utc)

            # Record reply timestamp for frequency limiter
            transcript.reply_runtime.assistant_reply_timestamps.append(
                last_assistant_reply_at.isoformat()
            )
            # Prune old timestamps outside the window
            window = float(config.orchestration.reply_frequency_window_seconds)
            cutoff_ts = last_assistant_reply_at.timestamp() - window
            transcript.reply_runtime.assistant_reply_timestamps = [
                ts for ts in transcript.reply_runtime.assistant_reply_timestamps
                if self._parse_runtime_datetime(ts) is not None
                and self._parse_runtime_datetime(ts).timestamp() >= cutoff_ts  # type: ignore[union-attr]
            ]

            if config.orchestration.enable_self_memory:
                context.self_memory_turn_counter += 1
                batch_size = max(1, int(config.orchestration.self_memory_extract_batch_size))
                min_chars = int(getattr(config.orchestration, "self_memory_min_chars", 0))
                reply_content = assistant_message.content
                trigger_by_count = context.self_memory_turn_counter % batch_size == 0
                trigger_by_chars = min_chars > 0 and len(reply_content) >= min_chars
                if trigger_by_count or trigger_by_chars:
                    logger.debug(
                        "自我记忆提取触发 | 轮次=%d batch=%d chars=%d",
                        context.self_memory_turn_counter,
                        batch_size,
                        len(reply_content),
                    )
                    await self._run_self_memory_extract_task(
                        config=config,
                        transcript=transcript,
                        context=context,
                        assistant_content=reply_content,
                    )
                    context.self_memory_turn_counter = 0
                    if context.self_memory_store is not None:
                        context.self_memory_store.save(context.self_memory)

            await context.event_bus.emit(SessionEvent(
                type=SessionEventType.PROCESSING_COMPLETED,
                message=assistant_message,
            ))
        else:
            logger.info(
                "[会话] 跳过回复 | speaker=%s | session_reply_mode=%s | engagement=%s",
                turn.speaker,
                resolved_session_mode,
                engagement.reason if engagement else "mode_never",
            )
            await context.event_bus.emit(SessionEvent(
                type=SessionEventType.REPLY_SKIPPED,
                data={"speaker": turn.speaker},
            ))

        transcript.reply_runtime.user_last_turn_at = {
            user_id: timestamp.isoformat()
            for user_id, timestamp in user_last_turn_at.items()
        }
        transcript.reply_runtime.group_recent_turn_timestamps = [
            timestamp.isoformat() for timestamp in group_recent_turns
        ]
        transcript.reply_runtime.last_assistant_reply_at = (
            last_assistant_reply_at.isoformat() if last_assistant_reply_at is not None else ""
        )

        if finalize_and_persist:
            await self._finalize_and_persist_live_context(
                config=config,
                transcript=transcript,
                context=context,
            )
        return transcript
