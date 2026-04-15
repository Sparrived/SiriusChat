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
    UserMemoryManager,
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
from sirius_chat.async_engine.orchestration import (
    TASK_MEMORY_EXTRACT,
    TASK_EVENT_EXTRACT,
    TASK_MEMORY_MANAGER,
    TASK_INTENT_ANALYSIS,
    SUPPORTED_MULTIMODAL_TYPES,
    get_system_prompt_for_task,
)
from sirius_chat.memory.self.models import DiaryEntry, GlossaryTerm
from sirius_chat.exceptions import OrchestrationConfigError
from sirius_chat.skills.registry import SkillRegistry
from sirius_chat.skills.executor import SkillExecutor, parse_skill_calls, strip_skill_calls
from sirius_chat.skills.models import SkillChainContext
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer
from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer
from sirius_chat.core.engagement import EngagementCoordinator, EngagementDecision
from sirius_chat.background_tasks import BackgroundTaskConfig, BackgroundTaskManager
from sirius_chat.core.markers import PROMPT_SPLIT_MARKER
from sirius_chat.core.memory_runner import (
    run_memory_extract_task,
    run_self_memory_extract_task,
    run_batch_event_extract,
    run_memory_manager_task,
    build_memory_extract_task_input,
)
from sirius_chat.core.engagement_pipeline import (
    build_heat_analysis,
    run_engagement_intent_analysis,
    should_reply_for_turn,
)
from sirius_chat.core.chat_builder import (
    has_multimodal_inputs,
    get_model_for_chat,
    is_internal_memory_metadata_line,
    sanitize_assistant_content,
    collect_internal_system_notes,
    build_chat_main_request_context,
)
from sirius_chat.token.store import TokenUsageStore
from sirius_chat.workspace.layout import WorkspaceLayout
logger = logging.getLogger(__name__)


from contextlib import asynccontextmanager
from typing import AsyncIterator


@asynccontextmanager
async def _noop_semaphore() -> AsyncIterator[None]:
    """No-op async context manager used when concurrency limiting is disabled."""
    yield


@dataclass(slots=True)
class SessionStores:
    """Storage layer: file-backed persistent stores (disk I/O boundary)."""
    file_store: UserMemoryFileStore
    event_file_store: EventMemoryFileStore
    token_store: TokenUsageStore | None = None
    self_memory_store: SelfMemoryFileStore | None = None


@dataclass(slots=True)
class SessionSubsystems:
    """Subsystem layer: in-memory domain managers and infrastructure services."""
    event_store: EventMemoryManager
    event_bus: SessionEventBus = field(default_factory=SessionEventBus)
    bg_task_manager: BackgroundTaskManager | None = None
    skill_registry: SkillRegistry | None = None
    skill_executor: SkillExecutor | None = None
    self_memory: SelfMemoryManager = field(default_factory=SelfMemoryManager)


@dataclass(slots=True)
class SessionCounters:
    """Runtime counters: message counts and token usage tracking."""
    task_token_usage: dict[str, int] = field(default_factory=dict)
    user_message_count_since_extract: dict[str, int] = field(default_factory=dict)
    self_memory_turn_counter: int = 0


@dataclass(slots=True)
class LiveSessionContext:
    """Per-session live context.

    Fields are organized into focused sub-objects by abstraction layer:
    - ``stores``: file-backed persistent stores (storage layer)
    - ``subsystems``: in-memory domain managers and infrastructure (subsystem layer)
    - ``counters``: runtime message/turn counters (counter layer)

    Remaining flat fields hold request-intermediate state and concurrency primitives.
    """
    stores: SessionStores
    subsystems: SessionSubsystems
    counters: SessionCounters = field(default_factory=SessionCounters)
    # ── Request intermediate state & participant registry ──
    known_by_id: dict[str, Participant] = field(default_factory=dict)
    known_by_label: dict[str, str] = field(default_factory=dict)
    # ── Concurrency control ──
    llm_semaphore: asyncio.Semaphore | None = None


@dataclass
class AsyncRolePlayEngine:
    provider: LLMProvider | AsyncLLMProvider
    _live_session_contexts: dict[int, LiveSessionContext] = field(default_factory=dict, init=False, repr=False)
    _orchestration_log_cache: set[str] = field(default_factory=set, init=False, repr=False)
    _shared_skill_registry: SkillRegistry | None = field(default=None, init=False, repr=False)
    _shared_skill_executor: SkillExecutor | None = field(default=None, init=False, repr=False)

    # ── Engine-level shared memory stores ──
    # Keyed by str(work_path) so that different sessions using the same
    # work_path share user memory, self-memory, and event memory.
    _shared_user_memory: dict[str, UserMemoryManager] = field(default_factory=dict, init=False, repr=False)
    _shared_self_memory: dict[str, SelfMemoryManager] = field(default_factory=dict, init=False, repr=False)
    _shared_event_stores: dict[str, EventMemoryManager] = field(default_factory=dict, init=False, repr=False)

    _TASK_TIMEOUT_SECONDS_DEFAULT = 45.0
    _TASK_TIMEOUT_SECONDS_CHAT_MAIN = 90.0

    # 所有需要模型支持的必需任务
    _REQUIRED_TASKS = [
        TASK_MEMORY_EXTRACT,
        TASK_EVENT_EXTRACT,
    ]

    @classmethod
    def _orchestration_log_models(cls, config: SessionConfig) -> dict[str, str]:
        orchestration = config.orchestration
        logged_tasks = list(cls._REQUIRED_TASKS)
        if (
            cls._resolve_session_reply_mode(config) == "auto"
            and orchestration.is_task_enabled(TASK_INTENT_ANALYSIS)
        ):
            logged_tasks.append(TASK_INTENT_ANALYSIS)

        resolved: dict[str, str] = {}
        for task in logged_tasks:
            model = orchestration.resolve_model_for_task(
                task,
                default_model=config.agent.model if task == TASK_INTENT_ANALYSIS else "",
            )
            if model:
                resolved[task] = model
        return resolved

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
                    "思维线路就绪，所有辅助任务将由 '%s' 统一承担",
                    orchestration.unified_model,
                )
                self._orchestration_log_cache.add(log_key)
            return
        
        if orchestration.task_models:
            # 方案2：按任务配置模型，但仅检查启用的任务
            enabled_tasks = [
                task for task in self._REQUIRED_TASKS
                if orchestration.is_task_enabled(task)
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
                task: bool(orchestration.is_task_enabled(task))
                for task in self._REQUIRED_TASKS
            }
            log_models = self._orchestration_log_models(config)
            log_key = (
                "task_models:"
                f"{json.dumps(dict(sorted(log_models.items())), ensure_ascii=False, sort_keys=True)}|"
                f"{json.dumps(enabled_flag, ensure_ascii=False, sort_keys=True)}"
            )
            if log_key not in self._orchestration_log_cache:
                logger.info(
                    "思维线路就绪，各辅助任务已分配专属模型 - %s",
                    log_models,
                )
                self._orchestration_log_cache.add(log_key)
            return

        raise OrchestrationConfigError(
            {task: [task] for task in self._REQUIRED_TASKS},
        )

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
        model = orchestration.resolve_model_for_task(
            task_name,
            default_model=config.agent.model if task_name == TASK_INTENT_ANALYSIS else "",
        )
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

    def set_shared_skill_runtime(
        self,
        *,
        skill_registry: SkillRegistry | None,
        skill_executor: SkillExecutor | None,
    ) -> None:
        self._shared_skill_registry = skill_registry
        self._shared_skill_executor = skill_executor

        for context in self._live_session_contexts.values():
            context.subsystems.skill_registry = skill_registry
            context.subsystems.skill_executor = skill_executor

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

        layout = WorkspaceLayout(config.data_path, config_path=config.work_path)
        work_key = str(layout.data_root)
        file_store = UserMemoryFileStore(layout)
        event_file_store = EventMemoryFileStore(layout)

        # ── Engine-level shared memory: load once, reuse across sessions ──
        if work_key in self._shared_user_memory:
            # Reuse engine-level user memory (already loaded from disk)
            shared_umem = self._shared_user_memory[work_key]
            transcript.user_memory.merge_from(shared_umem)
        else:
            # First session for this work_path — load from disk and cache
            loaded_manager = file_store.load_all()
            self._shared_user_memory[work_key] = loaded_manager
            transcript.user_memory.merge_from(loaded_manager)

        if work_key in self._shared_event_stores:
            event_store = self._shared_event_stores[work_key]
        else:
            event_store = event_file_store.load()
            self._shared_event_stores[work_key] = event_store

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
            stores=SessionStores(
                file_store=file_store,
                event_file_store=event_file_store,
                token_store=TokenUsageStore.for_workspace(layout, session_id=config.session_id),
            ),
            subsystems=SessionSubsystems(
                event_store=event_store,
            ),
            known_by_id=known_by_id,
            known_by_label=known_by_label,
        )

        # ── Engine-level shared self-memory ──
        if config.orchestration.enable_self_memory:
            if work_key in self._shared_self_memory:
                created.subsystems.self_memory = self._shared_self_memory[work_key]
            else:
                self_store = SelfMemoryFileStore(layout)
                created.subsystems.self_memory = self_store.load()
                self._shared_self_memory[work_key] = created.subsystems.self_memory
                # Apply diary decay on first load
                removed = created.subsystems.self_memory.apply_diary_decay()
                if removed > 0:
                    logger.info("%s 翻了翻旧日记，淡忘了 %d 条已久远的记忆碎片", config.agent.name, removed)
            created.stores.self_memory_store = SelfMemoryFileStore(layout)

        skills_dir = layout.skills_dir()
        SkillRegistry.ensure_skills_directory(skills_dir)
        if config.orchestration.enable_skills:
            if self._shared_skill_registry is not None:
                created.subsystems.skill_registry = self._shared_skill_registry
            else:
                registry = SkillRegistry()
                loaded_count = registry.reload_from_directory(
                    skills_dir,
                    auto_install_deps=config.orchestration.auto_install_skill_deps,
                )
                created.subsystems.skill_registry = registry
                if loaded_count > 0:
                    logger.info("%s 学会了 %d 项新技能，随时可以施展", config.agent.name, loaded_count)
                else:
                    logger.debug("SKILL系统已启用但未找到任何SKILL文件: %s", skills_dir)

            if self._shared_skill_executor is not None:
                created.subsystems.skill_executor = self._shared_skill_executor
            elif created.subsystems.skill_executor is None:
                created.subsystems.skill_executor = SkillExecutor(layout)
        else:
            logger.debug("SKILL系统已禁用，但已初始化SKILL目录: %s", skills_dir)

        # ── LLM concurrency semaphore ──
        max_concurrent = int(config.orchestration.max_concurrent_llm_calls)
        if max_concurrent > 0:
            created.llm_semaphore = asyncio.Semaphore(max_concurrent)

        # ── Background tasks: consolidation runs silently after live session startup ──
        bg_config = BackgroundTaskConfig(
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

        async def _consolidation_callback(
            _engine: AsyncRolePlayEngine = self,
            _config: SessionConfig = config,
            _transcript: Transcript = transcript,
            _ctx: LiveSessionContext = created,
        ) -> None:
            """Periodically consolidate events + notes + facts for all users."""
            if not _config.orchestration.is_task_enabled(TASK_MEMORY_MANAGER):
                return

            adapter = _engine._make_provider_adapter()
            try:
                model = _engine.get_model_for_task(_config, TASK_MEMORY_MANAGER)
            except ValueError:
                model = _config.agent.model

            for uid in _ctx.subsystems.event_store.get_all_user_ids():
                await _ctx.subsystems.event_store.consolidate_entries(
                    user_id=uid,
                    provider_async=adapter,
                    model_name=model,
                    min_entries=bg_config.consolidation_min_entries,
                )

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

            _ctx.stores.file_store.save_all(_transcript.user_memory)
            _ctx.stores.event_file_store.save(_ctx.subsystems.event_store)

            if _ctx.stores.self_memory_store is not None:
                removed = _ctx.subsystems.self_memory.apply_diary_decay()
                if removed > 0:
                    logger.info("在整理记忆的间隙，悄悄遗忘了 %d 条已褪色的往事", removed)
                _ctx.stores.self_memory_store.save(_ctx.subsystems.self_memory)

        bg_manager.set_consolidation_callback(_consolidation_callback)
        created.subsystems.bg_task_manager = bg_manager

        self._live_session_contexts[key] = created
        return created

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

    @staticmethod
    def _participant_from_profile(profile: object) -> "Participant":
        """Create a Participant from a UserProfile, copying all relevant fields."""
        return Participant(
            name=profile.name,  # type: ignore[attr-defined]
            user_id=profile.user_id,  # type: ignore[attr-defined]
            persona=profile.persona,  # type: ignore[attr-defined]
            identities=dict(profile.identities),  # type: ignore[attr-defined]
            aliases=list(profile.aliases),  # type: ignore[attr-defined]
            traits=list(profile.traits),  # type: ignore[attr-defined]
            metadata=dict(profile.metadata),  # type: ignore[attr-defined]
        )

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
                    participant = self._participant_from_profile(profile)
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
                    participant = self._participant_from_profile(profile)
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
        if context.subsystems.bg_task_manager is not None:
            await context.subsystems.bg_task_manager.stop()

        # 最终化事件记忆：对积累的事件进行 LLM 验证
        event_enabled = config.orchestration.task_enabled.get(TASK_EVENT_EXTRACT, True)
        pending_event_buffers = context.subsystems.event_store.pending_buffer_counts()
        if event_enabled and pending_event_buffers:
            estimated_cost = 512
            used = context.counters.task_token_usage.get(TASK_EVENT_EXTRACT, 0)
            try:
                event_model = self.get_model_for_task(config, TASK_EVENT_EXTRACT)
            except ValueError:
                event_model = config.agent.model
            try:
                finalize_result = await context.subsystems.event_store.finalize_pending_events(
                    provider_async=self._make_provider_adapter(),
                    model_name=event_model,
                    min_mentions=3,
                )
                context.counters.task_token_usage[TASK_EVENT_EXTRACT] = used + estimated_cost
                logger.info(
                    "%s 整理了一下记忆：确认了 %s 条，忘掉了 %s 条，还有 %s 条尚未想清楚",
                    config.agent.name,
                    finalize_result["verified_count"],
                    finalize_result["rejected_count"],
                    finalize_result["pending_count"],
                )
            except Exception as e:
                logger.warning(f"事件记忆最终化失败，继续执行: {e}")

        context.stores.file_store.save_all(transcript.user_memory)
        context.stores.event_file_store.save(context.subsystems.event_store)
        if context.stores.self_memory_store is not None:
            context.stores.self_memory_store.save(context.subsystems.self_memory)
        if context.subsystems.skill_executor is not None:
            context.subsystems.skill_executor.save_all_stores()

        # ── Sync back to engine-level shared stores ──
        work_key = str(WorkspaceLayout(config.data_path, config_path=config.work_path).data_root)
        self._shared_user_memory[work_key] = transcript.user_memory
        if config.orchestration.enable_self_memory:
            self._shared_self_memory[work_key] = context.subsystems.self_memory
        self._shared_event_stores[work_key] = context.subsystems.event_store

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
        if isinstance(self.provider, AsyncLLMProvider):
            return await self.provider.generate_async(request_payload)
        if isinstance(self.provider, LLMProvider):
            return await asyncio.to_thread(self.provider.generate, request_payload)
        raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")

    def _make_provider_adapter(self) -> object:
        """Return a lightweight async-provider adapter wrapping this engine.

        The returned object satisfies the ``provider_async`` protocol expected
        by memory / event sub-systems (``generate_async(request) -> str``).
        Calling code no longer needs to define an inline adapter class.
        """
        engine = self

        class _ProviderAdapter:
            async def generate_async(self, request: GenerationRequest) -> str:
                return await engine._call_provider(request)

        return _ProviderAdapter()

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
                if ctx is not None and ctx.stores.token_store is not None:
                    ctx.stores.token_store.add(record)
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
        await run_memory_extract_task(
            config=config,
            transcript=transcript,
            participant=participant,
            content=content,
            task_token_usage=task_token_usage,
            call_with_retry=self._call_provider_with_retry,
            get_model=self.get_model_for_task,
        )

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
            supported_types=SUPPORTED_MULTIMODAL_TYPES,
        )

    async def _run_self_memory_extract_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        context: LiveSessionContext,
        assistant_content: str,
    ) -> None:
        """Extract diary entries and glossary terms from the conversation."""
        await run_self_memory_extract_task(
            config=config,
            transcript=transcript,
            context=context,
            assistant_content=assistant_content,
            call_with_retry=self._call_provider_with_retry,
            get_model=self.get_model_for_task,
        )

    async def _run_batch_event_extract(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        task_token_usage: dict[str, int],
        event_store: EventMemoryManager,
    ) -> list[object]:
        """Batch-extract user observations from buffered messages."""
        return await run_batch_event_extract(
            config=config,
            transcript=transcript,
            participant=participant,
            task_token_usage=task_token_usage,
            event_store=event_store,
            make_adapter=self._make_provider_adapter,
            get_model=self.get_model_for_task,
        )

    async def _run_memory_manager_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        task_token_usage: dict[str, int],
    ) -> None:
        """汇聚、去重、标注、验证用户的记忆事实。"""
        await run_memory_manager_task(
            config=config,
            transcript=transcript,
            participant=participant,
            task_token_usage=task_token_usage,
            call_with_retry=self._call_provider_with_retry,
        )

    def _has_multimodal_inputs(self, transcript: Transcript) -> bool:
        """检测 transcript 中最后的用户消息是否包含多模态输入。"""
        return has_multimodal_inputs(transcript)

    # ── Engagement Decision System (v0.14.0) ──

    def _build_heat_analysis(
        self,
        *,
        transcript: Transcript,
        config: SessionConfig,
        group_recent_count: int,
    ) -> HeatAnalysis:
        """构建热度分析所需的数据并执行分析。"""
        return build_heat_analysis(
            transcript=transcript,
            config=config,
            group_recent_count=group_recent_count,
        )

    async def _run_engagement_intent_analysis(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        task_token_usage: dict[str, int],
    ) -> IntentAnalysis | None:
        """执行新版意图分析（携带参与者上下文）。"""
        return await run_engagement_intent_analysis(
            config=config,
            transcript=transcript,
            participant=participant,
            content=content,
            task_token_usage=task_token_usage,
            call_with_retry=self._call_provider_with_retry,
            get_model=self.get_model_for_task,
        )

    @staticmethod
    def _should_reply_for_turn(turn: Message) -> bool:
        """Check reply_mode: never → False, otherwise → True."""
        return should_reply_for_turn(turn)

    def _get_model_for_chat(self, config: SessionConfig, transcript: Transcript) -> str:
        """根据是否有多模态输入，动态选择主模型。"""
        return get_model_for_chat(config, transcript)

    @classmethod
    def _is_internal_memory_metadata_line(cls, line: str) -> bool:
        return is_internal_memory_metadata_line(line)

    def _sanitize_assistant_content(self, content: str) -> str:
        return sanitize_assistant_content(content)

    @staticmethod
    def _collect_internal_system_notes(transcript: Transcript) -> str:
        return collect_internal_system_notes(transcript)

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
        return build_chat_main_request_context(
            config=config,
            transcript=transcript,
            skill_descriptions=skill_descriptions,
            environment_context=environment_context,
            skip_sections=skip_sections,
            self_memory=self_memory,
        )

    @staticmethod
    def _build_memory_extract_task_input(
        *,
        transcript: Transcript,
        participant: Participant,
        content: str,
        max_context_messages: int = 8,
        max_context_chars: int = 1200,
    ) -> str:
        return build_memory_extract_task_input(
            transcript=transcript,
            participant=participant,
            content=content,
            max_context_messages=max_context_messages,
            max_context_chars=max_context_chars,
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
                        "正在施展『%s』（第 %d/%d 轮）",
                        skill_name, _round + 1, max_rounds,
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
                        PROMPT_SPLIT_MARKER
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
            marker = PROMPT_SPLIT_MARKER
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
        event_enabled = config.orchestration.task_enabled.get(TASK_EVENT_EXTRACT, True)
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
        if context.subsystems.bg_task_manager is not None and not context.subsystems.bg_task_manager.is_running():
            await context.subsystems.bg_task_manager.start()

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
        async for event in context.subsystems.event_bus.subscribe(max_queue_size=max_queue_size):
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
                async for evt in context.subsystems.event_bus.subscribe():
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
            if consume_task.done() or context.subsystems.event_bus.subscriber_count > 0:
                break
            await asyncio.sleep(0)
        if context.subsystems.event_bus.subscriber_count == 0:
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
        """Merge multiple buffered messages from the same user into one.

        Short messages (≤ 30 chars, single-line) are joined with ``，``
        to read as a natural continuation; longer or multi-line messages
        are joined with newlines to preserve structure.
        """
        if len(messages) == 1:
            return messages[0]

        parts: list[str] = [m.content for m in messages if m.content.strip()]
        if not parts:
            merged_content = ""
        elif all(len(p) <= 30 and "\n" not in p for p in parts):
            # All short single-line fragments → natural comma join
            merged_content = "，".join(parts)
        else:
            merged_content = "\n".join(parts)

        # Collect all multimodal inputs
        merged_multimodal: list[dict[str, str]] = []
        for m in messages:
            merged_multimodal.extend(m.multimodal_inputs)
        first = messages[0]
        last = messages[-1]
        return Message(
            role=first.role,
            content=merged_content,
            speaker=first.speaker,
            channel=first.channel,
            channel_user_id=first.channel_user_id,
            multimodal_inputs=merged_multimodal,
            reply_mode=last.reply_mode,
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

        event_hit_payload: dict[str, object] = {}

        # ── Parallel pre-processing pipeline ──
        # Run human-turn processing (memory_extract, event_extract) and intent
        # analysis concurrently to reduce total latency before the main LLM call.
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

        need_engagement = should_reply and resolved_session_mode in ("auto", "smart")

        # Build coroutines to run concurrently
        async def _run_add_human_turn() -> dict[str, object]:
            return await self._add_human_turn(
                config,
                transcript,
                participant,
                turn.content,
                task_token_usage=context.counters.task_token_usage,
                event_store=context.subsystems.event_store,
                known_entities=known_entities,
                channel=turn.channel,
                channel_user_id=turn.channel_user_id,
                multimodal_inputs=turn.multimodal_inputs,
                user_message_count_since_extract=context.counters.user_message_count_since_extract,
            )

        async def _run_intent_if_needed() -> IntentAnalysis | None:
            if not need_engagement:
                return None
            return await self._run_engagement_intent_analysis(
                config=config,
                transcript=transcript,
                participant=participant,
                content=turn.content,
                task_token_usage=context.counters.task_token_usage,
            )

        # Execute both in parallel
        event_hit_payload, intent = await asyncio.gather(
            _run_add_human_turn(),
            _run_intent_if_needed(),
        )

        assistant_interval_seconds: float | None = None
        if last_assistant_reply_at is not None:
            assistant_interval_seconds = max(
                0.0,
                (now - last_assistant_reply_at).total_seconds(),
            )

        # ── Engagement System: heat + intent → decision ──
        # Intent analysis was already gathered in the parallel pipeline above.
        engagement: EngagementDecision | None = None
        if need_engagement:
            heat = self._build_heat_analysis(
                transcript=transcript,
                config=config,
                group_recent_count=group_recent_count,
            )
            engagement = EngagementCoordinator.decide(
                heat=heat,
                intent=intent,
                sensitivity=float(config.orchestration.engagement_sensitivity),
            )
            should_reply = engagement.should_reply
            logger.info(
                "%s 说话了（热度:%s %.2f | 指向:%s | 参与度:%.3f）→ %s",
                turn.speaker,
                engagement.heat.heat_level if engagement.heat else "?",
                engagement.heat.heat_score if engagement.heat else 0.0,
                engagement.intent.target if engagement.intent else "?",
                engagement.engagement_score,
                engagement.reason,
            )

        # Emit user message event
        await context.subsystems.event_bus.emit(SessionEvent(
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
            await context.subsystems.event_bus.emit(SessionEvent(
                type=SessionEventType.REPLY_SKIPPED,
                data={"speaker": turn.speaker, "reason": "frequency_limit"},
            ))

        if should_reply:
            logger.info(
                "[会话] 触发回复 | speaker=%s | session_reply_mode=%s",
                turn.speaker,
                resolved_session_mode,
            )

            await context.subsystems.event_bus.emit(SessionEvent(
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
                    skill_registry=(
                        context.subsystems.skill_registry
                        if config.orchestration.enable_skills
                        else None
                    ),
                    skill_executor=(
                        context.subsystems.skill_executor
                        if config.orchestration.enable_skills
                        else None
                    ),
                    environment_context=environment_context,
                    event_bus=context.subsystems.event_bus,
                    skip_sections=intent.skip_sections if intent else [],
                    self_memory=context.subsystems.self_memory if config.orchestration.enable_self_memory else None,
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
                context.counters.self_memory_turn_counter += 1
                batch_size = max(1, int(config.orchestration.self_memory_extract_batch_size))
                min_chars = int(getattr(config.orchestration, "self_memory_min_chars", 0))
                reply_content = assistant_message.content
                trigger_by_count = context.counters.self_memory_turn_counter % batch_size == 0
                trigger_by_chars = min_chars > 0 and len(reply_content) >= min_chars
                if trigger_by_count or trigger_by_chars:
                    logger.debug(
                        "自我记忆提取触发 | 轮次=%d batch=%d chars=%d",
                        context.counters.self_memory_turn_counter,
                        batch_size,
                        len(reply_content),
                    )
                    await self._run_self_memory_extract_task(
                        config=config,
                        transcript=transcript,
                        context=context,
                        assistant_content=reply_content,
                    )
                    context.counters.self_memory_turn_counter = 0
                    if context.stores.self_memory_store is not None:
                        context.stores.self_memory_store.save(context.subsystems.self_memory)

            await context.subsystems.event_bus.emit(SessionEvent(
                type=SessionEventType.PROCESSING_COMPLETED,
                message=assistant_message,
            ))
        else:
            logger.info(
                "听到 %s 说话了，但这次选择静静旁观（%s）",
                turn.speaker,
                engagement.reason if engagement else "mode_never",
            )
            await context.subsystems.event_bus.emit(SessionEvent(
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
