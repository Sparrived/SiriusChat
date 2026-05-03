"""Prompt builders and generation for EmotionalGroupChatEngine."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sirius_chat.core.response_assembler import StyleParams
from sirius_chat.skills.executor import strip_skill_calls

logger = logging.getLogger(__name__)


class PromptBuildersMixin:
    """Mixin providing prompt builder and generation methods for EmotionalGroupChatEngine."""

    # ==================================================================
    # Prompt builders & generation
    # ==================================================================

    @staticmethod
    def _build_skill_result_content(
        skill_results: list[str],
        multimodal_blocks: list[dict[str, Any]],
        suffix: str = "",
    ) -> str | list[dict[str, Any]]:
        """Assemble skill execution results into message content.

        If *multimodal_blocks* is non-empty, returns an OpenAI-compatible
        ``content`` list (text + image_url parts) so local image paths are
        later converted to base64 data URLs by the transport layer.

        Long text results are truncated to avoid consuming excessive context
        window tokens, leaving room for the model's final reply.
        """
        _SKILL_RESULT_CHAR_LIMIT = 12000  # ~4k tokens, leaves headroom for reply
        results_text = "\n".join(skill_results)
        if len(results_text) > _SKILL_RESULT_CHAR_LIMIT:
            truncated = results_text[:_SKILL_RESULT_CHAR_LIMIT]
            # Try to cut at a newline boundary for cleanliness
            last_nl = truncated.rfind("\n")
            if last_nl > _SKILL_RESULT_CHAR_LIMIT * 0.8:
                truncated = truncated[:last_nl]
            results_text = (
                f"{truncated}\n\n"
                f"[注：技能结果过长，已截断至前 {_SKILL_RESULT_CHAR_LIMIT} 字符，"
                f"原始长度 {len(results_text)} 字符]"
            )
        text = f"[技能执行结果]\n{results_text}{suffix}"
        if not multimodal_blocks:
            return text
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content.extend(multimodal_blocks)
        return content

    def _build_delayed_prompt(
        self,
        items: Any,
        group_id: str,
        caller_is_developer: bool = False,
        adapter_type: str | None = None,
        is_first_interaction: bool = False,
    ):
        """Build prompt bundle for delayed response (supports single item or merged list)."""
        from sirius_chat.core.response_assembler import PromptBundle
        from sirius_chat.models.response_strategy import ResponseStrategy

        if not isinstance(items, list):
            items = [items]
        if len(items) == 1:
            message_content = items[0].message_content
        else:
            # Merge multiple messages into a single context
            lines = ["以下是你之前决定稍后回复的几条消息："]
            for idx, item in enumerate(items, 1):
                lines.append(f"{idx}. {item.message_content}")
            message_content = "\n".join(lines)
        glossary = self.glossary_manager.build_prompt_section(
            group_id, text=message_content, max_terms=5
        )
        # Collect relationship context for all users involved in the triggered batch
        related_uids: set[str] = set()
        for item in items:
            for uid in getattr(item, "related_user_ids", []):
                if uid:
                    related_uids.add(uid)
        delayed_user_profiles: list[Any] = []
        for uid in related_uids:
            prof = self.semantic_memory.get_user_profile(group_id, uid)
            if prof:
                delayed_user_profiles.append(prof)

        bundle = self.response_assembler.assemble_delayed(
            message_content=message_content,
            group_profile=self.semantic_memory.get_group_profile(group_id),
            is_group_chat=True,
            caller_is_developer=caller_is_developer,
            glossary_section=glossary,
            adapter_type=adapter_type,
            is_first_interaction=is_first_interaction,
            user_profiles=delayed_user_profiles,
        )
        return bundle

    def _pick_proactive_topic(self, group_id: str) -> str:
        """Pick a topic from semantic memory for proactive initiation.

        To avoid repetitive proactive messages, picks randomly from the
        top-N candidates rather than always returning the same highest-
        relevance topic.
        """
        import random

        group_profile = self.semantic_memory.get_group_profile(group_id)
        if group_profile is None:
            return ""

        # Collect candidate topics from group-level and user-level semantic memory
        candidates: list[str] = []

        # 1. Group-level interest topics
        if group_profile.interest_topics:
            candidates.extend(group_profile.interest_topics)

        # 2. User-level interest graphs (high participation topics)
        for profile in self.semantic_memory.list_group_user_profiles(group_id):
            for node in profile.interest_graph:
                if node.participation >= 0.3 and node.topic:
                    candidates.append(node.topic)

        # 3. Dominant topic from group norms (if available)
        dominant = group_profile.group_norms.get("dominant_topic", "")
        if dominant:
            candidates.append(dominant)

        if not candidates:
            return ""

        # Filter out taboo topics
        taboo = set(group_profile.taboo_topics or [])
        candidates = [t for t in candidates if t not in taboo]

        if not candidates:
            return ""

        # Deduplicate while preserving order (first occurrence = higher relevance)
        seen: set[str] = set()
        unique: list[str] = []
        for t in candidates:
            if t not in seen:
                seen.add(t)
                unique.append(t)

        # Pick randomly from top candidates to avoid always repeating the same topic.
        pool = unique[:3] if len(unique) >= 3 else unique
        return random.choice(pool)

    def _build_proactive_prompt(self, trigger: dict[str, Any], group_id: str, adapter_type: str | None = None):
        """Build prompt bundle for proactive initiation."""
        glossary = self.glossary_manager.build_prompt_section(
            group_id, text=trigger.get("trigger_type", ""), max_terms=3
        )
        topic = self._pick_proactive_topic(group_id)
        return self.response_assembler.assemble_proactive(
            trigger_reason=trigger.get("trigger_type", "silence"),
            group_profile=self.semantic_memory.get_group_profile(group_id),
            suggested_tone=trigger.get("suggested_tone", "casual"),
            is_group_chat=True,
            glossary_section=glossary,
            topic_context=topic,
            adapter_type=adapter_type,
        )

    async def _generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        group_id: str,
        style_params: StyleParams | None = None,
        task_name: str = "response_generate",
        urgency: int = 0,
        user_communication_style: str = "",
        token_breakdown: dict[str, int] | None = None,
    ) -> str:
        """Call LLM provider to generate response.

        Args:
            system_prompt: Instruction-level context (persona, emotion, skills, etc.).
            messages: Standard OpenAI-format conversation history ending with the
                current user message.
            group_id: Target group identifier.
            style_params: Optional style parameters (max_tokens, temperature).
            task_name: Cognitive task type for model routing.
            urgency: Urgency score (0-100) for dynamic escalation.
        """
        if self.provider_async is None:
            return "[未配置 provider]"

        # Tone alignment: adapt to current group emotional tone
        tone_hint = self._get_tone_alignment(group_id)
        if tone_hint:
            system_prompt = system_prompt + "\n\n" + tone_hint

        # Inject current time into system prompt (China timezone UTC+8)
        china_tz = timezone(timedelta(hours=8))
        now_str = datetime.now(china_tz).strftime("%Y-%m-%d %H:%M:%S")
        system_prompt = f"【当前时间】{now_str}（北京时间）\n\n{system_prompt}"

        # Model routing
        recent = self._get_recent_messages(group_id, n=5)
        rhythm = self.rhythm_analyzer.analyze(group_id, recent)
        cfg = self.model_router.resolve(
            task_name,
            urgency=urgency,
            heat_level=rhythm.heat_level,
            user_communication_style=user_communication_style,
        )

        # Apply style params if provided (override router's max_tokens)
        if style_params:
            effective_max_tokens = min(cfg.max_tokens, style_params.max_tokens)
            effective_temperature = style_params.temperature
        else:
            effective_max_tokens = cfg.max_tokens
            effective_temperature = cfg.temperature

        # No dual-output format; use token budget as-is

        # Build GenerationRequest
        from sirius_chat.providers.base import GenerationRequest, LLMProvider

        request = GenerationRequest(
            model=cfg.model_name,
            system_prompt=system_prompt.strip(),
            messages=messages,
            temperature=effective_temperature,
            max_tokens=effective_max_tokens,
            timeout_seconds=cfg.timeout,
            purpose=task_name,
        )

        # Estimate input tokens
        from sirius_chat.providers.base import estimate_generation_request_input_tokens

        estimated_input_tokens = estimate_generation_request_input_tokens(request)

        # Debug: log the full prompt sent to the LLM
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "LLM prompt for group=%s:\nSYSTEM:\n%s\n\nMESSAGES:\n%s",
                group_id,
                system_prompt,
                "\n".join(f"  [{m.get('role')}] {m.get('content', '')[:200]}" for m in messages),
            )

        # Call provider (async or sync via thread)
        reply = ""
        error_type = ""
        error_message = ""
        duration_ms = 0.0
        try:
            import asyncio
            import time

            t0 = time.perf_counter()
            if hasattr(self.provider_async, "generate_async"):
                reply = await self.provider_async.generate_async(request)
            elif isinstance(self.provider_async, LLMProvider):
                reply = await asyncio.to_thread(self.provider_async.generate, request)
            else:
                raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception as exc:
            error_type = self._classify_exception(exc)
            error_message = str(exc)[:200]
            LOG = logging.getLogger(__name__)
            LOG.warning("[%s] 生成失败: %s | %s", task_name, error_type, error_message)
            # Re-raise so caller can handle
            raise

        # Sanitise: strip any echoed <conversation_history> XML blocks
        reply = self._strip_conversation_history_xml(reply)

        # LLM 自选跳过：如果输出包含 <skip/>（忽略大小写与空白），则返回空
        if re.search(r"<\s*skip\s*/?\s*>", reply, flags=re.IGNORECASE):
            LOG = logging.getLogger(__name__)
            LOG.info("[%s] LLM 主动选择跳过回复（输出 skip 标签）。", task_name)
            reply = ""

        # Compute conversation depth
        import time

        now_ts = time.time()
        last_reply_ts = self._last_reply_at.get(group_id, 0)
        conversation_depth = (
            self._last_reply_depth.get(group_id, 0) + 1
            if now_ts - last_reply_ts < 60
            else 1
        )
        self._last_reply_depth[group_id] = conversation_depth

        # Record token usage
        from sirius_chat.config import TokenUsageRecord
        from sirius_chat.providers.base import get_last_generation_usage
        from sirius_chat.token.utils import estimate_tokens

        output_chars = len(reply)
        estimated_output_tokens = estimate_tokens(reply) if reply else 0
        real_usage = get_last_generation_usage()
        if real_usage and isinstance(real_usage, dict):
            prompt_tokens = int(real_usage.get("prompt_tokens", estimated_input_tokens))
            completion_tokens = int(real_usage.get("completion_tokens", estimated_output_tokens))
            total_tokens = int(real_usage.get("total_tokens", prompt_tokens + completion_tokens))
            estimation_method = "provider_real"
        else:
            prompt_tokens = estimated_input_tokens
            completion_tokens = estimated_output_tokens
            total_tokens = estimated_input_tokens + estimated_output_tokens
            estimation_method = "tiktoken" if estimated_output_tokens > 0 else "char_div4"

        persona_name = self.persona.name if self.persona else ""
        provider_name = getattr(self.provider_async, "_last_provider_name",
            getattr(self.provider_async, "_provider_name", "unknown"))

        # Build breakdown JSON if available
        breakdown_json = ""
        if token_breakdown:
            from sirius_chat.token.utils import PromptTokenBreakdown, estimate_tokens

            bd = PromptTokenBreakdown(**token_breakdown)
            bd.system_prompt_total = estimate_tokens(system_prompt)
            bd.user_message = sum(
                estimate_tokens(str(m.get("content", ""))) for m in messages
            )
            bd.output_total = completion_tokens
            bd.total = bd.system_prompt_total + bd.user_message + bd.output_total
            breakdown_json = bd.to_json()

        record = TokenUsageRecord(
            actor_id="assistant",
            task_name=task_name,
            model=cfg.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            input_chars=len(system_prompt)
            + sum(len(str(m.get("content", ""))) for m in messages),
            output_chars=output_chars,
            estimation_method=estimation_method,
            retries_used=0,
            persona_name=persona_name,
            group_id=group_id,
            provider_name=provider_name,
            breakdown_json=breakdown_json,
            duration_ms=duration_ms,
            conversation_depth=conversation_depth,
        )
        self.token_usage_records.append(record)

        if self.token_store is not None:
            try:
                self.token_store.add(record)
            except Exception:
                pass

        return reply
