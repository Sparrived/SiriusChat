from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
from typing import Awaitable, Callable, cast

from sirius_chat.config import SessionConfig, TokenUsageRecord
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest, LLMProvider
from sirius_chat.memory import (
    EventMemoryFileStore,
    EventMemoryManager,
    UserMemoryFileStore,
)
from sirius_chat.async_engine.utils import (
    build_event_hit_system_note,
    record_task_stat,
    estimate_tokens,
    extract_json_payload,
    normalize_multimodal_inputs,
)
from sirius_chat.async_engine.prompts import build_system_prompt
from sirius_chat.exceptions import OrchestrationConfigError

AsyncOnMessage = Callable[[Message], None]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ReplyWillingnessDecision:
    should_reply: bool
    score: float
    threshold: float
    reply_probability: float
    probability_roll: float
    intent_score: float
    addressing_score: float
    event_score: float
    richness_score: float
    user_cadence_penalty: float
    group_cadence_penalty: float
    assistant_cadence_penalty: float


@dataclass(slots=True)
class _PendingTurn:
    """Buffered user turn for debounce/batching."""
    participant_user_id: str
    messages: list[Message] = field(default_factory=list)
    timer_task: asyncio.Task[None] | None = None


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


@dataclass(slots=True)
class AsyncRolePlayEngine:
    provider: LLMProvider | AsyncLLMProvider
    _live_session_contexts: dict[int, LiveSessionContext] = field(default_factory=dict, init=False, repr=False)
    _orchestration_log_cache: set[str] = field(default_factory=set, init=False, repr=False)

    _TASK_MEMORY_EXTRACT = "memory_extract"
    _TASK_MULTIMODAL_PARSE = "multimodal_parse"
    _TASK_EVENT_EXTRACT = "event_extract"
    _TASK_MEMORY_MANAGER = "memory_manager"
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
        "multimodal_parse",
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
        )
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

    def _resolve_participant_for_turn(
        self,
        *,
        transcript: Transcript,
        turn: Message,
        context: LiveSessionContext,
    ) -> Participant:
        normalized = turn.speaker.strip().lower()
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
            participant = Participant(name=turn.speaker, user_id=turn.speaker, identities=identities)
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

    def _build_system_prompt(self, config: SessionConfig, transcript: Transcript) -> str:
        """Delegate to the prompts module for system prompt building."""
        return build_system_prompt(config, transcript)

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
                prompt_text = request_payload.system_prompt + "\n" + "\n".join(
                    item.get("content", "") for item in request_payload.messages
                )
                prompt_tokens = self._estimate_tokens(prompt_text)
                completion_tokens = self._estimate_tokens(content)
                transcript.add_token_usage_record(
                    TokenUsageRecord(
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
                )
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

    async def _run_multimodal_parse_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        multimodal_inputs: list[dict[str, str]],
        task_token_usage: dict[str, int],
    ) -> str | None:
        task_name = self._TASK_MULTIMODAL_PARSE
        # 检查任务是否启用
        if not config.orchestration.task_enabled.get(task_name, True):
            return None  # 任务被禁用
        
        model = self.get_model_for_task(config, task_name)

        normalized = self._normalize_multimodal_inputs(
            multimodal_inputs,
            max_items=max(1, int(config.orchestration.max_multimodal_inputs_per_turn)),
            max_value_length=max(1, int(config.orchestration.max_multimodal_value_length)),
        )
        if not normalized:
            self._record_task_stat(transcript, task_name, "skipped_invalid_input")
            return None

        self._record_task_stat(transcript, task_name, "attempted")

        system_prompt = (
            "你是多模态证据提取器。请阅读多模态输入说明并输出 JSON 对象，"
            "仅包含 evidence(string) 字段。"
        )
        task_input = (
            f"user_id={participant.user_id}\n"
            f"speaker={participant.name}\n"
            f"content={content}\n"
            f"multimodal_inputs={json.dumps(normalized, ensure_ascii=False)}"
        )
        estimated_cost = self._estimate_tokens(system_prompt + task_input)

        used = task_token_usage.get(task_name, 0)
        budget = int(config.orchestration.task_budgets.get(task_name, 0))
        if budget > 0 and used + estimated_cost > budget:
            self._record_task_stat(transcript, task_name, "skipped_budget")
            return None

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": task_input}],
            temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 256)),
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
            return None

        if retry_times > 0:
            self._record_task_stat(transcript, task_name, "retry_enabled")

        task_token_usage[task_name] = used + estimated_cost
        parsed = self._extract_json_payload(raw)
        if parsed is None:
            self._record_task_stat(transcript, task_name, "failed_parse")
            return None
        evidence = parsed.get("evidence")
        if not isinstance(evidence, str):
            self._record_task_stat(transcript, task_name, "failed_parse")
            return None
        evidence = evidence.strip()
        if not evidence:
            self._record_task_stat(transcript, task_name, "failed_parse")
            return None
        self._record_task_stat(transcript, task_name, "succeeded")
        return evidence

    async def _run_event_extract_task(
        self,
        *,
        config: SessionConfig,
        transcript: Transcript,
        participant: Participant,
        content: str,
        task_token_usage: dict[str, int],
    ) -> dict[str, object] | None:
        task_name = self._TASK_EVENT_EXTRACT
        # 检查任务是否启用
        if not config.orchestration.task_enabled.get(task_name, True):
            return None  # 任务被禁用
        
        model = self.get_model_for_task(config, task_name)

        self._record_task_stat(transcript, task_name, "attempted")
        system_prompt = (
            "你是事件提取器。请基于输入提取结构化事件信息，严格输出 JSON 对象，"
            "只允许字段：summary(string)、keywords(array[string])、role_slots(array[string])、"
            "entities(array[string])、time_hints(array[string])、emotion_tags(array[string])。"
        )
        task_input = (
            f"user_id={participant.user_id}\n"
            f"speaker={participant.name}\n"
            f"content={content}"
        )
        estimated_cost = self._estimate_tokens(system_prompt + task_input)

        used = task_token_usage.get(task_name, 0)
        budget = int(config.orchestration.task_budgets.get(task_name, 0))
        if budget > 0 and used + estimated_cost > budget:
            self._record_task_stat(transcript, task_name, "skipped_budget")
            return None

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": task_input}],
            temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 192)),
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
            return None

        if retry_times > 0:
            self._record_task_stat(transcript, task_name, "retry_enabled")

        task_token_usage[task_name] = used + estimated_cost
        parsed = self._extract_json_payload(raw)
        if parsed is None:
            self._record_task_stat(transcript, task_name, "failed_parse")
            return None

        keywords_raw = parsed.get("keywords")
        role_slots_raw = parsed.get("role_slots")
        entities_raw = parsed.get("entities")
        time_hints_raw = parsed.get("time_hints")
        emotion_tags_raw = parsed.get("emotion_tags")

        cleaned = {
            "summary": str(parsed.get("summary", "")).strip(),
            "keywords": [str(item).strip() for item in keywords_raw if str(item).strip()]
            if isinstance(keywords_raw, list)
            else [],
            "role_slots": [str(item).strip() for item in role_slots_raw if str(item).strip()]
            if isinstance(role_slots_raw, list)
            else [],
            "entities": [str(item).strip() for item in entities_raw if str(item).strip()]
            if isinstance(entities_raw, list)
            else [],
            "time_hints": [str(item).strip() for item in time_hints_raw if str(item).strip()]
            if isinstance(time_hints_raw, list)
            else [],
            "emotion_tags": [str(item).strip() for item in emotion_tags_raw if str(item).strip()]
            if isinstance(emotion_tags_raw, list)
            else [],
        }
        self._record_task_stat(transcript, task_name, "succeeded")
        return cleaned

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

    @staticmethod
    def _compute_intent_score(content: str) -> float:
        text = content.strip()
        if not text:
            return 0.0

        lowered = text.lower()
        score = 0.0
        if "?" in text or "？" in text:
            score += 0.30

        request_markers = (
            "请",
            "帮我",
            "麻烦",
            "可以",
            "能不能",
            "如何",
            "怎么",
            "为什么",
            "总结",
            "建议",
            "分析",
            "please",
            "could you",
            "can you",
            "how",
            "why",
        )
        if any(marker in lowered for marker in request_markers):
            score += 0.25

        return max(0.0, min(0.5, score))

    @staticmethod
    def _compute_addressing_score(*, content: str, agent_name: str, agent_alias: str) -> float:
        text = content.strip()
        if not text:
            return 0.0

        lowered = text.lower()
        names = [agent_name.strip(), agent_alias.strip()]
        for name in names:
            if name and name.lower() in lowered:
                return 0.20

        if "@" in text:
            return 0.16

        if "你" in text or "您" in text:
            return 0.08
        return 0.0

    @staticmethod
    def _compute_event_relevance_score(event_hit_payload: dict[str, object] | None) -> float:
        if not event_hit_payload:
            return 0.0
        level = str(event_hit_payload.get("level", "")).strip().lower()
        score = float(event_hit_payload.get("score", 0.0) or 0.0)

        if level == "high":
            return 0.20
        if level == "weak":
            return 0.12
        if level == "new":
            return 0.06 + min(0.04, score * 0.04)
        return min(0.08, max(0.0, score) * 0.10)

    @staticmethod
    def _compute_richness_score(content: str) -> float:
        text = content.strip()
        if not text:
            return 0.0
        if len(text) >= 96:
            return 0.10
        if len(text) >= 48:
            return 0.07
        if len(text) >= 24:
            return 0.04
        return 0.0

    @staticmethod
    def _deterministic_probability_roll(*, turn: Message, group_recent_count: int) -> float:
        seed = (
            f"{turn.speaker or ''}|{turn.channel or ''}|{turn.channel_user_id or ''}|"
            f"{group_recent_count}|{turn.content.strip()}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    @classmethod
    def _evaluate_reply_willingness(
        cls,
        *,
        turn: Message,
        config: SessionConfig,
        event_hit_payload: dict[str, object] | None,
        user_interval_seconds: float | None,
        group_recent_count: int,
        assistant_interval_seconds: float | None,
    ) -> ReplyWillingnessDecision:
        content = turn.content
        if not content.strip():
            return ReplyWillingnessDecision(
                should_reply=False,
                score=0.0,
                threshold=0.58,
                reply_probability=0.0,
                probability_roll=1.0,
                intent_score=0.0,
                addressing_score=0.0,
                event_score=0.0,
                richness_score=0.0,
                user_cadence_penalty=0.0,
                group_cadence_penalty=0.0,
                assistant_cadence_penalty=0.0,
            )

        agent_alias = str(config.agent.metadata.get("alias", "")).strip()
        intent_score = cls._compute_intent_score(content)
        addressing_score = cls._compute_addressing_score(
            content=content,
            agent_name=config.agent.name,
            agent_alias=agent_alias,
        )
        event_score = cls._compute_event_relevance_score(event_hit_payload)
        richness_score = cls._compute_richness_score(content)

        orchestration = config.orchestration
        user_cadence_seconds = max(0.5, float(orchestration.auto_reply_user_cadence_seconds))
        group_penalty_start_count = max(0, int(orchestration.auto_reply_group_penalty_start_count))
        assistant_cooldown_seconds = max(0.5, float(orchestration.auto_reply_assistant_cooldown_seconds))

        # 用户发言频率因子：默认目标间隔 7 秒（覆盖 6~8 秒需求）
        user_cadence_penalty = 0.0
        if user_interval_seconds is not None and user_interval_seconds < user_cadence_seconds:
            cadence_ratio = max(
                0.0,
                min(1.0, (user_cadence_seconds - user_interval_seconds) / user_cadence_seconds),
            )
            # 明确请求时降低频率惩罚，让“强请求”仍可能得到回复
            user_cadence_penalty = 0.35 * cadence_ratio
            if intent_score >= 0.35:
                user_cadence_penalty *= 0.35

        # 群聊密度因子：8 秒窗口内消息越多，越倾向潜水
        group_cadence_penalty = 0.0
        if group_recent_count > group_penalty_start_count:
            density_ratio = max(
                0.0,
                min(1.0, (group_recent_count - group_penalty_start_count) / 4.0),
            )
            group_cadence_penalty = 0.30 * density_ratio
            if intent_score >= 0.45:
                group_cadence_penalty *= 0.40

        # AI 刚回复过时，若没有明确请求则降低参与度
        assistant_cadence_penalty = 0.0
        if (
            assistant_interval_seconds is not None
            and assistant_interval_seconds < assistant_cooldown_seconds
            and intent_score < 0.35
        ):
            assistant_cadence_penalty = 0.15 * max(
                0.0,
                min(
                    1.0,
                    (assistant_cooldown_seconds - assistant_interval_seconds)
                    / assistant_cooldown_seconds,
                ),
            )

        score = (
            float(orchestration.auto_reply_base_score)
            + intent_score
            + addressing_score
            + event_score
            + richness_score
            - user_cadence_penalty
            - group_cadence_penalty
            - assistant_cadence_penalty
        )
        score = max(0.0, min(1.0, score))

        threshold = float(orchestration.auto_reply_threshold)
        if group_recent_count >= int(orchestration.auto_reply_threshold_boost_start_count):
            threshold += 0.06
        if intent_score >= 0.35:
            threshold -= 0.08
        if event_score >= 0.12:
            threshold -= 0.03
        threshold = max(
            float(orchestration.auto_reply_threshold_min),
            min(float(orchestration.auto_reply_threshold_max), threshold),
        )

        should_reply = score >= threshold
        reply_probability = 1.0 if should_reply else 0.0
        probability_roll = 1.0
        if not should_reply:
            probability_coefficient = max(
                0.0,
                min(1.0, float(orchestration.auto_reply_probability_coefficient)),
            )
            if probability_coefficient > 0.0:
                probability_floor = max(
                    0.0,
                    min(1.0, float(orchestration.auto_reply_probability_floor)),
                )
                reply_probability = max(
                    probability_floor,
                    min(1.0, score * probability_coefficient),
                )
                # 明确点名主 AI 时，提高兜底回复概率，减少“被叫到但未回应”的体验。
                if addressing_score >= 0.20:
                    reply_probability = max(reply_probability, 0.80)
                probability_roll = cls._deterministic_probability_roll(
                    turn=turn,
                    group_recent_count=group_recent_count,
                )
                should_reply = probability_roll < reply_probability

        return ReplyWillingnessDecision(
            should_reply=should_reply,
            score=score,
            threshold=threshold,
            reply_probability=reply_probability,
            probability_roll=probability_roll,
            intent_score=intent_score,
            addressing_score=addressing_score,
            event_score=event_score,
            richness_score=richness_score,
            user_cadence_penalty=user_cadence_penalty,
            group_cadence_penalty=group_cadence_penalty,
            assistant_cadence_penalty=assistant_cadence_penalty,
        )

    @classmethod
    def _should_reply_for_turn(
        cls,
        *,
        turn: Message,
        config: SessionConfig,
        event_hit_payload: dict[str, object] | None = None,
        user_interval_seconds: float | None = None,
        group_recent_count: int = 1,
        assistant_interval_seconds: float | None = None,
    ) -> tuple[bool, ReplyWillingnessDecision | None]:
        mode = str(getattr(turn, "reply_mode", "always") or "always").strip().lower()
        if mode in {"never", "silent", "none", "no_reply"}:
            return False, None
        if mode in {"auto", "smart"}:
            decision = cls._evaluate_reply_willingness(
                turn=turn,
                config=config,
                event_hit_payload=event_hit_payload,
                user_interval_seconds=user_interval_seconds,
                group_recent_count=group_recent_count,
                assistant_interval_seconds=assistant_interval_seconds,
            )
            return decision.should_reply, decision
        # unknown mode falls back to always
        return True, None

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
    ) -> tuple[str, list[dict[str, str]]]:
        system_prompt = self._build_system_prompt(config, transcript)
        internal_notes = self._collect_internal_system_notes(transcript)
        if internal_notes:
            system_prompt = (
                f"{system_prompt}\n\n"
                "[会话内部系统补充]\n"
                "以下为引擎内部记录的系统上下文，用于辅助推理；"
                "请勿在最终回复中逐字复述。\n"
                f"{internal_notes}"
            )

        chat_history = [
            item for item in transcript.as_chat_history()
            if str(item.get("role", "")).strip().lower() != "system"
        ]
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

    async def _generate_assistant_message(self, config: SessionConfig, transcript: Transcript) -> Message:
        if config.enable_auto_compression:
            transcript.compress_for_budget(
                max_messages=config.history_max_messages,
                max_chars=config.history_max_chars,
            )
        
        # 动态选择模型：有多模态输入时自动升级到多模态模型
        model = self._get_model_for_chat(config, transcript)
        system_prompt, chat_history = self._build_chat_main_request_context(
            config=config,
            transcript=transcript,
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
                            content=part_stripped,
                            speaker=speaker,
                        )
                        transcript.add(msg)
                        last_message = msg
                        # 在消息之间增加小延迟，模拟实时聊天
                        if part != parts[-1]:  # 不是最后一条
                            await asyncio.sleep(0.01)
        
        # 如果没有分割标记，或未启用分割，则按常规处理
        if last_message is None:
            assistant_message = Message(
                role="assistant",
                content=content,
                speaker=speaker,
            )
            transcript.add(assistant_message)
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

        event_task: asyncio.Task[dict[str, object] | None] | None = asyncio.create_task(
            self._run_event_extract_task(
                config=config,
                transcript=transcript,
                participant=participant,
                content=content,
                task_token_usage=task_token_usage,
            )
        )

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

        multimodal_task: asyncio.Task[str | None] = asyncio.create_task(
            self._run_multimodal_parse_task(
                config=config,
                transcript=transcript,
                participant=participant,
                content=content,
                multimodal_inputs=normalized_multimodal_inputs,
                task_token_usage=task_token_usage,
            )
        )

        pending_tasks: list[asyncio.Task[object]] = [multimodal_task]
        if memory_extract_task is not None:
            pending_tasks.append(cast(asyncio.Task[object], memory_extract_task))
        if event_task is not None:
            pending_tasks.append(cast(asyncio.Task[object], event_task))
        await asyncio.gather(*pending_tasks)

        evidence = multimodal_task.result()
        extracted_event_features = event_task.result() if event_task is not None else None
        if evidence:
            transcript.add(
                Message(
                    role="system",
                    content=f"多模态解析证据[{participant.name}]：{evidence}",
                )
            )
            transcript.user_memory.apply_ai_runtime_update(
                user_id=participant.user_id,
                summary_note=f"多模态证据：{evidence[:48]}",
                source="multimodal_parse",
                confidence=0.75,
            )
        hit_payload = event_store.absorb_mention(
            content=content,
            known_entities=known_entities,
            extracted_features=extracted_event_features,
        )
        transcript.add(
            Message(
                role="system",
                content=build_event_hit_system_note(speaker=participant.name, hit_payload=hit_payload),
            )
        )
        event_entry = hit_payload.get("entry")
        if event_entry is not None and extracted_event_features is not None:
            summary = str(getattr(event_entry, "summary", "")).strip()
            if summary:
                transcript.user_memory.apply_ai_runtime_update(
                    user_id=participant.user_id,
                    summary_note=f"事件摘要：{summary[:48]}",
                    source="event_extract",
                    confidence=0.65,
                )
            
            # ✨ 新增：方案C - 事件到用户记忆的双向适配
            # 1. 将事件特征转化为用户记忆事实
            if extracted_event_features:
                transcript.user_memory.apply_event_insights(
                    user_id=participant.user_id,
                    event_features=extracted_event_features,
                    source="event_extract",
                    base_confidence=0.65,
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
        on_message: AsyncOnMessage | None = None,
        transcript: Transcript | None = None,
    ) -> Transcript:
        # 验证多模型协同配置
        self.validate_orchestration_config(config)
        
        _ = on_message
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
        _ = self._get_or_create_live_context(config=config, transcript=transcript)
        return transcript

    async def run_live_message(
        self,
        config: SessionConfig,
        turn: Message,
        on_message: AsyncOnMessage | None = None,
        transcript: Transcript | None = None,
        session_reply_mode: str | None = None,
        finalize_and_persist: bool = True,
    ) -> Transcript:
        self.validate_orchestration_config(config)

        transcript = self._prepare_transcript(config, transcript)
        if turn.role != "user" or not turn.speaker:
            raise ValueError("run_live_message 仅接受带 speaker 的单条 user 消息。")

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
                # Same user — append to buffer, reset timer
                pending.messages.append(turn)
                if pending.timer_task is not None and not pending.timer_task.done():
                    pending.timer_task.cancel()
                pending.timer_task = None
                # Wait for debounce window
                try:
                    await asyncio.sleep(debounce_seconds)
                except asyncio.CancelledError:
                    return transcript
                # After sleep, check if we are still the latest (no newer message appended)
                if context.pending_turn is pending and pending.messages[-1] is turn:
                    # Timer expired and we are still the latest → flush
                    merged_turn = self._merge_pending_turns(pending.messages)
                    context.pending_turn = None
                    return await self._process_live_turn(
                        config=config,
                        turn=merged_turn,
                        on_message=on_message,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=finalize_and_persist,
                        context=context,
                        participant=participant,
                    )
                return transcript
            else:
                # Different user or first message — flush pending if any, then buffer new
                if pending is not None and pending.messages:
                    # Cancel any pending timer
                    if pending.timer_task is not None and not pending.timer_task.done():
                        pending.timer_task.cancel()
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
                        on_message=on_message,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=False,
                        context=context,
                        participant=old_participant,
                    )
                # Buffer new turn
                context.pending_turn = _PendingTurn(
                    participant_user_id=participant.user_id,
                    messages=[turn],
                )
                # Wait for debounce window
                try:
                    await asyncio.sleep(debounce_seconds)
                except asyncio.CancelledError:
                    return transcript
                # After sleep, check if still the latest
                if context.pending_turn is not None and context.pending_turn.participant_user_id == participant.user_id and context.pending_turn.messages[-1] is turn:
                    merged_turn = self._merge_pending_turns(context.pending_turn.messages)
                    context.pending_turn = None
                    return await self._process_live_turn(
                        config=config,
                        turn=merged_turn,
                        on_message=on_message,
                        transcript=transcript,
                        session_reply_mode=session_reply_mode,
                        finalize_and_persist=finalize_and_persist,
                        context=context,
                        participant=participant,
                    )
                return transcript

        # No debounce — process immediately
        return await self._process_live_turn(
            config=config,
            turn=turn,
            on_message=on_message,
            transcript=transcript,
            session_reply_mode=session_reply_mode,
            finalize_and_persist=finalize_and_persist,
            context=context,
            participant=participant,
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
        on_message: AsyncOnMessage | None,
        transcript: Transcript,
        session_reply_mode: str | None,
        finalize_and_persist: bool,
        context: LiveSessionContext,
        participant: Participant,
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
        group_window_start = now.timestamp() - float(config.orchestration.auto_reply_group_window_seconds)
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
        should_reply, willingness = self._should_reply_for_turn(
            turn=effective_turn,
            config=config,
            event_hit_payload=event_hit_payload,
            user_interval_seconds=user_interval_seconds,
            group_recent_count=group_recent_count,
            assistant_interval_seconds=assistant_interval_seconds,
        )
        if should_reply:
            if willingness is not None:
                reply_trigger = (
                    "threshold"
                    if willingness.score >= willingness.threshold
                    else "probability_fallback"
                )
                logger.info(
                    "[会话] 触发回复 | speaker=%s | session_reply_mode=%s | trigger=%s | "
                    "score=%.3f | threshold=%.3f | probability=%.3f | roll=%.3f",
                    turn.speaker,
                    resolved_session_mode,
                    reply_trigger,
                    willingness.score,
                    willingness.threshold,
                    willingness.reply_probability,
                    willingness.probability_roll,
                )
            assistant_message = await self._generate_assistant_message(config, transcript)
            last_assistant_reply_at = datetime.now(timezone.utc)
            if on_message:
                on_message(assistant_message)
        else:
            if willingness is None:
                logger.info(
                    "[会话] 跳过回复 | speaker=%s | session_reply_mode=%s",
                    turn.speaker,
                    resolved_session_mode,
                )
            else:
                logger.info(
                    "[会话] 跳过回复 | speaker=%s | session_reply_mode=%s | score=%.3f | threshold=%.3f | "
                    "probability=%.3f | roll=%.3f | "
                    "intent=%.3f | addr=%.3f | event=%.3f | richness=%.3f | "
                    "penalty_user=%.3f | penalty_group=%.3f | penalty_assistant=%.3f | "
                    "user_interval=%.2fs | group_recent_count=%d",
                    turn.speaker,
                    resolved_session_mode,
                    willingness.score,
                    willingness.threshold,
                    willingness.reply_probability,
                    willingness.probability_roll,
                    willingness.intent_score,
                    willingness.addressing_score,
                    willingness.event_score,
                    willingness.richness_score,
                    willingness.user_cadence_penalty,
                    willingness.group_cadence_penalty,
                    willingness.assistant_cadence_penalty,
                    user_interval_seconds or -1.0,
                    group_recent_count,
                )

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
