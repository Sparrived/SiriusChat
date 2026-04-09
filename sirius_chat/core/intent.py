"""Intent analysis for conversation context optimization.

Analyzes user intent to:
1. Adjust reply willingness score with an intent-based modifier
2. Determine which system prompt sections can be skipped
3. Optimize context window by removing unnecessary information
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

INTENT_TYPES = frozenset({
    "question",
    "request",
    "chat",
    "reaction",
    "information_share",
    "command",
})

_INTENT_SYSTEM_PROMPT = (
    "你是意图分析器。分析用户消息的意图，严格输出 JSON 对象：\n"
    '{"intent_type":"question|request|chat|reaction|information_share|command",'
    '"directed_at_ai":bool,'
    '"importance":float(0-1),'
    '"needs_memory":bool,'
    '"needs_summary":bool,'
    '"reason":string,'
    '"evidence_span":string}\n'
    "- intent_type: 消息意图类型\n"
    "- directed_at_ai: 是否指向AI\n"
    "- importance: 需要AI回复的紧迫程度(0-1)\n"
    "- needs_memory: 回复是否需要参考参与者记忆\n"
    "- needs_summary: 回复是否需要会话摘要上下文\n"
    "- reason: 1 句话解释判断依据\n"
    "- evidence_span: 从用户原话中摘取的关键短语\n"
    "不要输出任何额外文字。"
)


@dataclass(slots=True)
class IntentAnalysis:
    """Result of intent analysis for a user message."""

    intent_type: str = "chat"
    confidence: float = 0.5
    directed_at_ai: bool = True
    willingness_modifier: float = 0.0
    skip_sections: list[str] = field(default_factory=list)
    reason: str = ""
    evidence_span: str = ""


class IntentAnalyzer:
    """Analyzes user intent to optimize reply willingness and prompt construction."""

    @staticmethod
    async def analyze(
        *,
        content: str,
        agent_name: str,
        agent_alias: str,
        recent_messages: list[dict[str, str]],
        call_provider: Callable[..., Awaitable[str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 128,
    ) -> IntentAnalysis:
        """Analyze user intent using LLM.

        Args:
            content: The user message to analyze.
            agent_name: AI agent name.
            agent_alias: AI agent alias.
            recent_messages: Recent chat history for context.
            call_provider: Async callable that takes a GenerationRequest and returns str.
            model: Model name for intent analysis.
            temperature: LLM temperature.
            max_tokens: Max tokens for response.

        Returns:
            IntentAnalysis with intent classification and modifiers.
        """
        from sirius_chat.providers.base import GenerationRequest

        context_text = ""
        if recent_messages:
            lines = []
            for msg in recent_messages[-5:]:
                role = msg.get("role", "")
                text = msg.get("content", "")[:100]
                lines.append(f"[{role}] {text}")
            context_text = "\n".join(lines)

        user_prompt = (
            f"AI名称: {agent_name}"
            + (f" (别名: {agent_alias})" if agent_alias else "")
            + f"\n当前消息: {content[:300]}"
            + (f"\n近期上下文:\n{context_text}" if context_text else "")
        )

        request = GenerationRequest(
            model=model,
            system_prompt=_INTENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="intent_analysis",
        )

        try:
            raw = await call_provider(request)
            return IntentAnalyzer._parse_response(raw)
        except Exception as exc:
            logger.warning("意图分析 LLM 调用失败，使用回退: %s", exc)
            return IntentAnalyzer.fallback_analysis(content, agent_name, agent_alias)

    @staticmethod
    def _parse_response(raw: str) -> IntentAnalysis:
        """Parse LLM response into IntentAnalysis."""
        text = raw.strip()
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "意图分析响应解析失败（非 JSON）: %s",
                text[:200].replace("\n", " "),
            )
            return IntentAnalysis()

        if not isinstance(data, dict):
            logger.warning("意图分析响应解析失败（JSON 非对象）: %s", type(data).__name__)
            return IntentAnalysis()

        intent_type = str(data.get("intent_type", "chat")).strip().lower()
        if intent_type not in INTENT_TYPES:
            intent_type = "chat"

        directed_at_ai = bool(data.get("directed_at_ai", True))
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))
        needs_memory = bool(data.get("needs_memory", True))
        needs_summary = bool(data.get("needs_summary", True))
        reason = str(data.get("reason", "")).strip()
        evidence_span = str(data.get("evidence_span", "")).strip()

        if len(reason) > 200:
            reason = reason[:200]
        if len(evidence_span) > 120:
            evidence_span = evidence_span[:120]

        # Compute willingness modifier
        willingness_modifier = 0.0
        if intent_type in ("question", "request", "command"):
            willingness_modifier = 0.1 + importance * 0.2
        elif intent_type == "reaction":
            willingness_modifier = -0.1
        elif intent_type == "information_share":
            willingness_modifier = -0.05

        if not directed_at_ai:
            willingness_modifier -= 0.15

        willingness_modifier = max(-0.2, min(0.3, willingness_modifier))

        # Determine sections to skip
        skip_sections: list[str] = []
        if not needs_memory:
            skip_sections.append("participant_memory")
        if not needs_summary:
            skip_sections.append("session_summary")

        return IntentAnalysis(
            intent_type=intent_type,
            confidence=importance,
            directed_at_ai=directed_at_ai,
            willingness_modifier=willingness_modifier,
            skip_sections=skip_sections,
            reason=reason,
            evidence_span=evidence_span,
        )

    @staticmethod
    def fallback_analysis(content: str, agent_name: str, agent_alias: str) -> IntentAnalysis:
        """Fast keyword-based fallback when LLM is unavailable."""
        text = content.strip().lower()
        original_text = content.strip()

        directed = False
        for name in (agent_name.lower(), agent_alias.lower()):
            if name and name in text:
                directed = True
                break
        if "@" in text or "你" in text or "您" in text:
            directed = True

        intent_type = "chat"
        willingness_modifier = 0.0
        reason = "未命中特殊规则，按普通聊天处理。"
        evidence_span = ""

        if "?" in content or "？" in content:
            intent_type = "question"
            willingness_modifier = 0.15
            reason = "消息包含疑问符号，判定为提问。"
            evidence_span = "?" if "?" in content else "？"
        elif any(m in text for m in ("请", "帮我", "帮忙", "麻烦", "please", "can you", "could you")):
            intent_type = "request"
            willingness_modifier = 0.2
            reason = "消息包含请求关键词，判定为请求。"
            for marker in ("请", "帮我", "帮忙", "麻烦", "please", "can you", "could you"):
                if marker in text:
                    evidence_span = marker
                    break
        elif len(text) < 10 and any(m in text for m in ("好", "嗯", "ok", "哈", "哦", "噢", "行")):
            intent_type = "reaction"
            willingness_modifier = -0.1
            reason = "短文本且命中反馈词，判定为反应类消息。"
            for marker in ("好", "嗯", "ok", "哈", "哦", "噢", "行"):
                if marker in text:
                    evidence_span = marker
                    break

        if not directed:
            willingness_modifier -= 0.1
            if reason == "未命中特殊规则，按普通聊天处理。":
                reason = "未检测到明确指向 AI 的线索，降低回复意愿。"
        elif not evidence_span and original_text:
            evidence_span = original_text[:24]

        return IntentAnalysis(
            intent_type=intent_type,
            confidence=0.5,
            directed_at_ai=directed,
            willingness_modifier=max(-0.2, min(0.3, willingness_modifier)),
            reason=reason,
            evidence_span=evidence_span,
        )


__all__ = [
    "INTENT_TYPES",
    "IntentAnalysis",
    "IntentAnalyzer",
]
