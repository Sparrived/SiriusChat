"""意图分析子系统 v2

深度分析用户消息的聊天对象、内容指向性与意图，解决群聊中
AI 无法正确识别对话对象的核心问题。

核心改进:
- 显式判断消息指向 (target): ai / others / everyone / unknown
- 上下文感知：携带近期对话与参与者列表做推断
- LLM 路径增强：要求模型解释判断理由与证据片段
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from sirius_chat.providers.base import GenerationRequest

logger = logging.getLogger(__name__)

INTENT_TYPES = frozenset({
    "question",
    "request",
    "chat",
    "reaction",
    "information_share",
    "command",
})

TARGET_TYPES = frozenset({"ai", "others", "everyone", "unknown"})

_INTENT_SYSTEM_PROMPT = (
    "你是一个对话意图分析器。你的任务是分析群聊中的每条消息，判断说话者在跟谁对话、意图是什么。\n"
    "严格输出 JSON 对象：\n"
    "{\n"
    '  "intent_type": "question|request|chat|reaction|information_share|command",\n'
    '  "target": "ai|others|everyone|unknown",\n'
    '  "importance": float(0-1),\n'
    '  "needs_memory": bool,\n'
    '  "needs_summary": bool,\n'
    '  "reason": "一句话解释你的判断依据",\n'
    '  "evidence_span": "从原消息中摘取的关键短语"\n'
    "}\n\n"
    "判断指南：\n"
    "- target=ai：消息明确指向AI（提及AI名字/别名、使用\"你\"且上下文指向AI、对AI说话）\n"
    "- target=others：消息明确指向群内其他人（提及其他人名字、@其他人、回复其他人话题）\n"
    "- target=everyone：消息面向全体（公告、一般感叹、分享信息）\n"
    "- target=unknown：无法确定指向\n\n"
    "重要规则：\n"
    "- 仅凭\"你\"字不能判定指向AI，必须结合上下文确认\n"
    "- 如果上一条消息是某个人说的，当前消息可能在回复那个人而非AI\n"
    "- 当群聊中有多人对话时，要根据话题连续性判断说话对象\n"
    "- 不要输出任何额外文字\n"
)


@dataclass(slots=True)
class IntentAnalysis:
    """意图分析结果。"""

    intent_type: str = "chat"
    target: str = "unknown"          # ai | others | everyone | unknown
    confidence: float = 0.5
    directed_at_ai: bool = False     # 便利属性：target == "ai"
    importance: float = 0.5
    skip_sections: list[str] = field(default_factory=list)
    reason: str = ""
    evidence_span: str = ""


class IntentAnalyzer:
    """分析用户消息意图，支持 LLM 深度分析与关键词快速回退。"""

    @staticmethod
    async def analyze(
        *,
        content: str,
        agent_name: str,
        agent_alias: str,
        participant_names: list[str],
        recent_messages: list[dict[str, str]],
        call_provider: Callable[..., Awaitable[str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 192,
    ) -> IntentAnalysis:
        """通过 LLM 深度分析消息意图与聊天对象。

        Args:
            content: 待分析消息内容。
            agent_name: AI 名称。
            agent_alias: AI 别名。
            participant_names: 群内其他参与者名称列表。
            recent_messages: 近期聊天历史 [{role, content, speaker?}]。
            call_provider: 异步 LLM 调用函数。
            model: 分析用模型名。
        """
        request = IntentAnalyzer.build_request(
            content=content,
            agent_name=agent_name,
            agent_alias=agent_alias,
            participant_names=participant_names,
            recent_messages=recent_messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            raw = await call_provider(request)
            return IntentAnalyzer._parse_response(raw)
        except Exception as exc:
            logger.warning("意图分析 LLM 调用失败，使用回退: %s", exc)
            return IntentAnalyzer.fallback_analysis(
                content, agent_name, agent_alias, participant_names,
            )

    @staticmethod
    def build_request(
        *,
        content: str,
        agent_name: str,
        agent_alias: str,
        participant_names: list[str],
        recent_messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 192,
    ) -> GenerationRequest:
        """Build a GenerationRequest for the intent analysis task."""

        # 构造丰富的上下文
        context_lines: list[str] = []
        if recent_messages:
            for msg in recent_messages[-8:]:
                role = msg.get("role", "")
                speaker = msg.get("speaker", "")
                text = msg.get("content", "")[:120]
                label = speaker if speaker else role
                context_lines.append(f"[{label}] {text}")

        participants_info = f"群内参与者：{', '.join(participant_names)}" if participant_names else ""

        user_prompt = (
            f"AI名称: {agent_name}"
            + (f" (别名: {agent_alias})" if agent_alias else "")
            + (f"\n{participants_info}" if participants_info else "")
            + (f"\n\n近期对话:\n" + "\n".join(context_lines) if context_lines else "")
            + f"\n\n当前消息: {content[:400]}"
        )

        return GenerationRequest(
            model=model,
            system_prompt=_INTENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="intent_analysis",
        )

    @staticmethod
    def _parse_response(raw: str) -> IntentAnalysis:
        """解析 LLM JSON 响应。"""
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

        target = str(data.get("target", "unknown")).strip().lower()
        if target not in TARGET_TYPES:
            target = "unknown"

        directed_at_ai = target == "ai"
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))
        needs_memory = bool(data.get("needs_memory", True))
        needs_summary = bool(data.get("needs_summary", True))
        reason = str(data.get("reason", "")).strip()
        evidence_span = str(data.get("evidence_span", "")).strip()

        if len(reason) > 200:
            reason = reason[:200]
        if len(evidence_span) > 120:
            evidence_span = evidence_span[:120]

        skip_sections: list[str] = []
        if not needs_memory:
            skip_sections.append("participant_memory")
        if not needs_summary:
            skip_sections.append("session_summary")

        return IntentAnalysis(
            intent_type=intent_type,
            target=target,
            confidence=importance,
            directed_at_ai=directed_at_ai,
            importance=importance,
            skip_sections=skip_sections,
            reason=reason,
            evidence_span=evidence_span,
        )

    @staticmethod
    def fallback_analysis(
        content: str,
        agent_name: str,
        agent_alias: str,
        participant_names: list[str] | None = None,
    ) -> IntentAnalysis:
        """关键词快速回退分析（零 LLM 开销）。

        增强版：当消息中提到了其他参与者名字时，target 判定为 others。
        """
        text = content.strip().lower()
        original_text = content.strip()

        # ── 判断 target ──
        # 优先检查是否提及 AI
        ai_directed = False
        for name in (agent_name.lower(), agent_alias.lower()):
            if name and name in text:
                ai_directed = True
                break

        # 检查是否提及其他参与者（排除 AI 名字）
        other_directed = False
        if participant_names:
            ai_names = {agent_name.lower(), agent_alias.lower()} - {""}
            for pname in participant_names:
                pname_lower = pname.strip().lower()
                if pname_lower and pname_lower not in ai_names and pname_lower in text:
                    other_directed = True
                    break

        # @ 提及检测
        if "@" in text:
            # 如果 @了AI名字 → ai_directed
            for name in (agent_name.lower(), agent_alias.lower()):
                if name and f"@{name}" in text:
                    ai_directed = True
                    break
            else:
                # @了别人
                other_directed = True

        # 代词推断 —— 仅在没有明确提及他人时才考虑
        pronoun_hint = ("你" in text or "您" in text) and not other_directed

        # 确定 target
        if ai_directed:
            target = "ai"
        elif other_directed:
            target = "others"
        elif pronoun_hint:
            # 有「你」但没有明确指向 → 标记为 unknown 而非 ai
            # 这是核心修复：避免群聊中对他人说「你」时被误判为指向 AI
            target = "unknown"
        else:
            target = "everyone"

        directed_at_ai = target == "ai"

        # ── 判断 intent_type ──
        intent_type = "chat"
        reason = "未命中特殊规则，按普通聊天处理。"
        evidence_span = ""

        if "?" in content or "？" in content:
            intent_type = "question"
            reason = "消息包含疑问符号，判定为提问。"
            evidence_span = "?" if "?" in content else "？"
        elif any(m in text for m in ("请", "帮我", "帮忙", "麻烦", "please", "can you", "could you")):
            intent_type = "request"
            reason = "消息包含请求关键词，判定为请求。"
            for marker in ("请", "帮我", "帮忙", "麻烦", "please", "can you", "could you"):
                if marker in text:
                    evidence_span = marker
                    break
        elif len(text) < 10 and any(m in text for m in ("好", "嗯", "ok", "哈", "哦", "噢", "行")):
            intent_type = "reaction"
            reason = "短文本且命中反馈词，判定为反应类消息。"
            for marker in ("好", "嗯", "ok", "哈", "哦", "噢", "行"):
                if marker in text:
                    evidence_span = marker
                    break

        if not directed_at_ai and target == "others":
            reason = f"消息指向其他参与者，非 AI 对话。{reason}"
        elif not evidence_span and original_text:
            evidence_span = original_text[:24]

        return IntentAnalysis(
            intent_type=intent_type,
            target=target,
            confidence=0.5,
            directed_at_ai=directed_at_ai,
            importance=0.5 if directed_at_ai else 0.3,
            reason=reason,
            evidence_span=evidence_span,
        )


__all__ = [
    "INTENT_TYPES",
    "TARGET_TYPES",
    "IntentAnalysis",
    "IntentAnalyzer",
]
