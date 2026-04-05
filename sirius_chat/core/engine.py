from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
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
    EVENT_DEDUP_WINDOW_MINUTES,
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
class AsyncRolePlayEngine:
    provider: LLMProvider | AsyncLLMProvider

    _TASK_MEMORY_EXTRACT = "memory_extract"
    _TASK_MULTIMODAL_PARSE = "multimodal_parse"
    _TASK_EVENT_EXTRACT = "event_extract"
    _TASK_MEMORY_MANAGER = "memory_manager"
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
            logger.info(
                f"多模型协同（方案1）：所有任务共用模型 '{orchestration.unified_model}'"
            )
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
            logger.info(
                f"多模型协同（方案2）：按任务配置模型 - {orchestration.task_models}"
            )
            return
    
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
        for index in range(attempts):
            try:
                content = await self._call_provider(request_payload)
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
            return

        request_payload = GenerationRequest(
            model=model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": task_input}],
            temperature=float(config.orchestration.task_temperatures.get(task_name, 0.1)),
            max_tokens=int(config.orchestration.task_max_tokens.get(task_name, 128)),
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

    async def _generate_assistant_message(self, config: SessionConfig, transcript: Transcript) -> Message:
        if config.enable_auto_compression:
            transcript.compress_for_budget(
                max_messages=config.history_max_messages,
                max_chars=config.history_max_chars,
            )
        
        # 动态选择模型：有多模态输入时自动升级到多模态模型
        model = self._get_model_for_chat(config, transcript)
        
        request_payload = GenerationRequest(
            model=model,
            system_prompt=self._build_system_prompt(config, transcript),
            messages=transcript.as_chat_history(),
            temperature=config.agent.temperature,
            max_tokens=config.agent.max_tokens,
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
        # 如果响应以 "[{speaker_name}] " 开头，移除之
        speaker = str(config.agent.metadata.get("alias", "")).strip() or config.agent.name
        speaker_prefix_patterns = [
            f"[{speaker}] ",  # 当前配置的 speaker
        ]
        # 也检查是否有其他常见的前缀格式
        for pattern in speaker_prefix_patterns:
            if content.startswith(pattern):
                content = content[len(pattern):]
                break

        content = self._sanitize_assistant_content(content)
        
        last_message: Message | None = None
        
        if config.orchestration.enable_prompt_driven_splitting:
            marker = config.orchestration.split_marker
            if marker in content:
                # 识别到分割标记，拆分消息
                parts = content.split(marker)
                for part in parts:
                    part_stripped = part.strip()
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
    ) -> None:
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
            await self._run_memory_extract_task(
                config=config,
                transcript=transcript,
                participant=participant,
                content=content,
                task_token_usage=task_token_usage,
            )
            user_message_count_since_extract[participant.user_id] = 0
        else:
            user_message_count_since_extract[participant.user_id] = current_count + 1
        
        evidence = await self._run_multimodal_parse_task(
            config=config,
            transcript=transcript,
            participant=participant,
            content=content,
            multimodal_inputs=normalized_multimodal_inputs,
            task_token_usage=task_token_usage,
        )
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

        # ============================================================================
        # A1方案: 时间窗口去重 - 防止短时间内重复调用event_extract
        # ============================================================================
        user_memory_entry = transcript.user_memory.entries.get(participant.user_id)
        should_process_event = True
        
        if user_memory_entry is not None:
            last_processed = user_memory_entry.runtime.last_event_processed_at
            if last_processed is not None:
                # 计算距离上次处理的时间（分钟）
                time_since_last = (datetime.now(timezone.utc) - last_processed).total_seconds() / 60
                
                # 如果在去重窗口内，则跳过事件处理
                if time_since_last < EVENT_DEDUP_WINDOW_MINUTES:
                    should_process_event = False
                    logger.info(
                        f"[去重] 用户{participant.name}在{time_since_last:.1f}分钟内重复消息，跳过事件处理"
                    )

        # 根据去重结果决定是否执行event_extract
        if should_process_event:
            extracted_event_features = await self._run_event_extract_task(
                config=config,
                transcript=transcript,
                participant=participant,
                content=content,
                task_token_usage=task_token_usage,
            )
            # 更新最后处理时间戳
            if user_memory_entry is not None:
                user_memory_entry.runtime.last_event_processed_at = datetime.now(timezone.utc)
        else:
            # 去重模式：不调用event_extract，但仍然保存消息到内存
            extracted_event_features = None
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
        human_turns: list[Message],
        on_message: AsyncOnMessage | None = None,
        transcript: Transcript | None = None,
    ) -> Transcript:
        # 验证多模型协同配置
        self.validate_orchestration_config(config)
        
        transcript = self._prepare_transcript(config, transcript)
        file_store = UserMemoryFileStore(config.work_path)
        event_file_store = EventMemoryFileStore(config.work_path)
        event_store = event_file_store.load()
        transcript.user_memory.merge_from(file_store.load_all())
        task_token_usage: dict[str, int] = {}
        # 跟踪每个用户自上次memory_extract后的消息计数
        user_message_count_since_extract: dict[str, int] = {}

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

        for turn in human_turns:
            if turn.role != "user" or not turn.speaker:
                raise ValueError("run_live_session 仅接受带 speaker 的 user 消息。")
            normalized = turn.speaker.strip().lower()
            participant = None

            if turn.channel and turn.channel_user_id:
                mapped_user_id = transcript.user_memory.resolve_user_id(
                    channel=turn.channel,
                    external_user_id=turn.channel_user_id,
                )
                if mapped_user_id:
                    participant = known_by_id.get(mapped_user_id)
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
                        known_by_id[participant.user_id] = participant

            resolved_id = known_by_label.get(normalized)
            if resolved_id and participant is None:
                participant = known_by_id.get(resolved_id)
            if participant is None:
                memory_user_id = transcript.user_memory.resolve_user_id(speaker=turn.speaker)
                if memory_user_id:
                    participant = known_by_id.get(memory_user_id)
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
                        known_by_id[participant.user_id] = participant
            if participant is None:
                identities = {}
                if turn.channel and turn.channel_user_id:
                    identities[turn.channel] = turn.channel_user_id
                participant = Participant(name=turn.speaker, user_id=turn.speaker, identities=identities)
                known_by_id[participant.user_id] = participant

            labels = [participant.name, participant.user_id, *participant.aliases]
            for label in labels:
                if label:
                    known_by_label[label.strip().lower()] = participant.user_id

            known_entities: list[str] = []
            for item in known_by_id.values():
                values = [item.name, item.user_id, *item.aliases]
                for value in values:
                    text = value.strip()
                    if text and text not in known_entities:
                        known_entities.append(text)

            await self._add_human_turn(
                config,
                transcript,
                participant,
                turn.content,
                task_token_usage=task_token_usage,
                event_store=event_store,
                known_entities=known_entities,
                channel=turn.channel,
                channel_user_id=turn.channel_user_id,
                multimodal_inputs=turn.multimodal_inputs,
                user_message_count_since_extract=user_message_count_since_extract,
            )
            assistant_message = await self._generate_assistant_message(config, transcript)
            if on_message:
                on_message(assistant_message)

        # 最终化事件记忆：对积累的事件进行 LLM 验证
        try:
            # 创建一个临时的 AsyncLLMProvider 包装器以支持同步 provider
            class ProviderAdapter:
                def __init__(self, engine: AsyncRolePlayEngine) -> None:
                    self.engine = engine
                
                async def generate_async(self, request: GenerationRequest) -> str:
                    return await self.engine._call_provider(request)
            
            finalize_result = await event_store.finalize_pending_events(
                provider_async=ProviderAdapter(self),
                model_name=config.agent.model,
                min_mentions=3
            )
            logger.info(f"事件记忆最终化完成 - 已验证: {finalize_result['verified_count']}, 已拒绝: {finalize_result['rejected_count']}, 待验证: {finalize_result['pending_count']}")
        except Exception as e:
            logger.warning(f"事件记忆最终化失败，继续执行: {e}")

        file_store.save_all(transcript.user_memory)
        event_file_store.save(event_store)
        return transcript
