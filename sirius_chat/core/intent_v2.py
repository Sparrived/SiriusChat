"""意图分析子系统 v2

深度分析用户消息的聊天对象、内容指向性与意图，解决群聊中
AI 无法正确识别对话对象的核心问题。

核心改进:
- 显式判断消息指向 (target): ai / others / everyone / unknown
- 在 target=ai 内进一步细分 target_scope: self_ai / other_ai
- 上下文感知：携带近期对话与参与者列表做推断
- LLM 路径增强：要求模型解释判断理由与证据片段
"""

from __future__ import annotations

import json
import logging
import re
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
TARGET_SCOPES = frozenset({"self_ai", "other_ai", "human", "everyone", "unknown"})

_INTENT_SYSTEM_PROMPT = (
    "你是一个对话意图分析器。你的任务是分析群聊中的每条消息，判断说话者在跟谁对话、意图是什么。\n"
    "严格输出 JSON 对象：\n"
    "{\n"
    '  "intent_type": "question|request|chat|reaction|information_share|command",\n'
    '  "target": "ai|others|everyone|unknown",\n'
    '  "target_scope": "self_ai|other_ai|human|everyone|unknown",\n'
    '  "importance": float(0-1),\n'
    '  "needs_memory": bool,\n'
    '  "needs_summary": bool,\n'
    '  "reason": "一句话解释你的判断依据",\n'
    '  "evidence_span": "从原消息中摘取的关键短语"\n'
    "}\n\n"
    "判断指南：\n"
    "- target=ai 且 target_scope=self_ai：消息明确指向当前模型自身（提及当前模型名字/别名、使用\"你\"且上下文指向当前模型、直接回复当前模型）\n"
    "- target=ai 且 target_scope=other_ai：消息明确指向群内其他 AI，而不是当前模型\n"
    "- target=others 且 target_scope=human：消息明确指向群内其他人类参与者（提及其他人名字、@其他人、回复其他人话题）\n"
    "- target=everyone：消息面向全体（公告、一般感叹、分享信息）\n"
    "- target=unknown：无法确定指向\n\n"
    "重要规则：\n"
    "- 仅凭\"你\"字不能判定指向AI，必须结合上下文确认\n"
    "- 群聊里可能有多个 AI，必须优先判断消息是指向当前模型自身，还是指向其他 AI\n"
    "- 如果当前消息命中了人类名字或其他AI名字，且没有命中当前模型名字，通常不能判定为 self_ai\n"
    "- 如果上一条消息是某个人说的，当前消息可能在回复那个人而非AI\n"
    "- 当群聊中有多人对话时，要根据话题连续性判断说话对象\n"
    "- 不要输出任何额外文字\n"
)

_INTENT_CONTEXT_MESSAGE_LIMIT = 4
_INTENT_CONTEXT_TEXT_LIMIT = 48
_PRONOUN_CONTEXT_TURN_LIMIT = 4


@dataclass(slots=True)
class IntentAnalysis:
    """意图分析结果。"""

    intent_type: str = "chat"
    target: str = "unknown"          # ai | others | everyone | unknown
    target_scope: str = "unknown"    # self_ai | other_ai | human | everyone | unknown
    confidence: float = 0.5
    directed_at_ai: bool = False     # 便利属性：target == "ai"
    directed_at_current_ai: bool = False
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
            parsed = IntentAnalyzer._parse_response(raw)
            if parsed is not None:
                return parsed
            logger.warning("意图分析响应解析失败，使用回退。")
            return IntentAnalyzer.fallback_analysis(
                content, agent_name, agent_alias, participant_names, recent_messages,
            )
        except Exception as exc:
            logger.warning("意图分析 LLM 调用失败，使用回退: %s", exc)
            return IntentAnalyzer.fallback_analysis(
                content, agent_name, agent_alias, participant_names, recent_messages,
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

        current_ai_names = [name for name in (agent_name, agent_alias) if str(name).strip()]
        other_ai_names = IntentAnalyzer._extract_other_ai_names(
            recent_messages=recent_messages,
            agent_name=agent_name,
            agent_alias=agent_alias,
        )
        self_hits = IntentAnalyzer._extract_name_hits(content, current_ai_names)
        other_ai_hits = IntentAnalyzer._extract_name_hits(content, other_ai_names)
        human_hits = IntentAnalyzer._extract_name_hits(content, participant_names)
        context_lines = IntentAnalyzer._summarize_recent_messages(recent_messages)
        recent_ai_speakers = IntentAnalyzer._extract_recent_speakers(
            recent_messages=recent_messages,
            role="assistant",
            limit=3,
        )
        recent_human_speakers = IntentAnalyzer._extract_recent_speakers(
            recent_messages=recent_messages,
            role="user",
            limit=4,
        )

        participants_info = f"群内人类参与者：{', '.join(participant_names[:8])}" if participant_names else ""
        current_ai_info = f"当前模型自身：{', '.join(current_ai_names)}" if current_ai_names else ""
        other_ai_info = f"近期其他AI发言者：{', '.join(other_ai_names[:4])}" if other_ai_names else ""
        recent_ai_info = f"最近AI发言者（近到远）：{', '.join(recent_ai_speakers)}" if recent_ai_speakers else ""
        recent_human_info = f"最近人类发言者（近到远）：{', '.join(recent_human_speakers)}" if recent_human_speakers else ""
        self_hit_info = f"当前消息命中的当前模型名字：{', '.join(self_hits)}" if self_hits else ""
        other_ai_hit_info = f"当前消息命中的其他AI名字：{', '.join(other_ai_hits)}" if other_ai_hits else ""
        human_hit_info = f"当前消息命中的人类名字：{', '.join(human_hits)}" if human_hits else ""

        user_prompt = (
            f"AI名称: {agent_name}"
            + (f" (别名: {agent_alias})" if agent_alias else "")
            + (f"\n{current_ai_info}" if current_ai_info else "")
            + (f"\n{other_ai_info}" if other_ai_info else "")
            + (f"\n{participants_info}" if participants_info else "")
            + (f"\n{recent_ai_info}" if recent_ai_info else "")
            + (f"\n{recent_human_info}" if recent_human_info else "")
            + (f"\n{self_hit_info}" if self_hit_info else "")
            + (f"\n{other_ai_hit_info}" if other_ai_hit_info else "")
            + (f"\n{human_hit_info}" if human_hit_info else "")
            + (f"\n\n最近交互链摘要:\n" + "\n".join(context_lines) if context_lines else "")
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
    def _parse_response(raw: str) -> IntentAnalysis | None:
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
            return None

        if not isinstance(data, dict):
            logger.warning("意图分析响应解析失败（JSON 非对象）: %s", type(data).__name__)
            return None

        intent_type = str(data.get("intent_type", "chat")).strip().lower()
        if intent_type not in INTENT_TYPES:
            intent_type = "chat"

        target = str(data.get("target", "unknown")).strip().lower()
        target_scope = str(data.get("target_scope", "")).strip().lower()
        target, target_scope = IntentAnalyzer._normalize_target_fields(target, target_scope)

        directed_at_ai = target == "ai"
        directed_at_current_ai = target_scope == "self_ai"
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
            target_scope=target_scope,
            confidence=importance,
            directed_at_ai=directed_at_ai,
            directed_at_current_ai=directed_at_current_ai,
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
        recent_messages: list[dict[str, str]] | None = None,
    ) -> IntentAnalysis:
        """关键词快速回退分析（零 LLM 开销）。

        增强版：区分当前模型自身、其他 AI 与其他参与者。
        """
        text = content.strip().lower()
        original_text = content.strip()
        current_ai_names = [name for name in (agent_name, agent_alias) if str(name).strip()]
        other_ai_names = IntentAnalyzer._extract_other_ai_names(
            recent_messages=recent_messages or [],
            agent_name=agent_name,
            agent_alias=agent_alias,
        )
        human_names = [str(name).strip() for name in (participant_names or []) if str(name).strip()]

        # ── 判断 target ──
        self_ai_hits = IntentAnalyzer._extract_name_hits(content, current_ai_names)
        other_ai_hits = IntentAnalyzer._extract_name_hits(content, other_ai_names)
        other_human_hits = IntentAnalyzer._extract_name_hits(content, human_names)
        self_ai_directed = bool(self_ai_hits)
        other_ai_directed = bool(other_ai_hits)
        other_human_directed = bool(other_human_hits)

        # @ 提及检测
        if "@" in text:
            for name in current_ai_names:
                if f"@{name}" in text:
                    self_ai_directed = True
                    break
            for name in other_ai_names:
                if f"@{name}" in text:
                    other_ai_directed = True
                    break
            for name in human_names:
                if f"@{name}" in text:
                    other_human_directed = True
                    break

        # 代词推断 —— 仅在没有明确提及他人时才考虑
        pronoun_hint = (
            ("你" in text or "您" in text)
            and not self_ai_directed
            and not other_ai_directed
            and not other_human_directed
        )

        # 确定 target
        if self_ai_directed:
            target = "ai"
            target_scope = "self_ai"
        elif other_ai_directed:
            target = "ai"
            target_scope = "other_ai"
        elif other_human_directed:
            target = "others"
            target_scope = "human"
        elif pronoun_hint:
            inferred_scope = IntentAnalyzer._infer_pronoun_target_scope(
                recent_messages=recent_messages or [],
                current_ai_names={name.lower() for name in current_ai_names},
                other_ai_names={name.lower() for name in other_ai_names},
                human_names={name.lower() for name in human_names},
            )
            if inferred_scope == "self_ai":
                target = "ai"
                target_scope = "self_ai"
            elif inferred_scope == "other_ai":
                target = "ai"
                target_scope = "other_ai"
            elif inferred_scope == "human":
                target = "others"
                target_scope = "human"
            else:
                target = "unknown"
                target_scope = "unknown"
        else:
            target = "everyone"
            target_scope = "everyone"

        directed_at_ai = target == "ai"
        directed_at_current_ai = target_scope == "self_ai"

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

        if target_scope == "other_ai":
            reason = f"消息更像是在对其他AI说话，而不是当前模型。{reason}"
        elif not directed_at_ai and target == "others":
            reason = f"消息指向其他参与者，非 AI 对话。{reason}"
        elif not evidence_span and original_text:
            evidence_span = original_text[:24]

        return IntentAnalysis(
            intent_type=intent_type,
            target=target,
            target_scope=target_scope,
            confidence=0.5,
            directed_at_ai=directed_at_ai,
            directed_at_current_ai=directed_at_current_ai,
            importance=0.5 if directed_at_current_ai else 0.3,
            reason=reason,
            evidence_span=evidence_span,
        )

    @staticmethod
    def _normalize_target_fields(target: str, target_scope: str) -> tuple[str, str]:
        normalized_target = target.strip().lower()
        normalized_scope = target_scope.strip().lower()

        if normalized_target == "self_ai":
            normalized_target = "ai"
            normalized_scope = "self_ai"
        elif normalized_target == "other_ai":
            normalized_target = "ai"
            normalized_scope = "other_ai"

        if normalized_scope == "self":
            normalized_scope = "self_ai"
        elif normalized_scope == "other":
            normalized_scope = "other_ai"

        if normalized_target not in TARGET_TYPES:
            normalized_target = "unknown"

        if normalized_scope not in TARGET_SCOPES:
            if normalized_target == "ai":
                normalized_scope = "self_ai"
            elif normalized_target == "others":
                normalized_scope = "human"
            elif normalized_target == "everyone":
                normalized_scope = "everyone"
            else:
                normalized_scope = "unknown"

        if normalized_scope in {"self_ai", "other_ai"}:
            normalized_target = "ai"
        elif normalized_scope == "human":
            normalized_target = "others"
        elif normalized_scope == "everyone":
            normalized_target = "everyone"

        return normalized_target, normalized_scope

    @staticmethod
    def _extract_other_ai_names(
        *,
        recent_messages: list[dict[str, str]],
        agent_name: str,
        agent_alias: str,
    ) -> list[str]:
        current_ai_names = {
            str(name).strip().lower()
            for name in (agent_name, agent_alias)
            if str(name).strip()
        }
        other_ai_names: list[str] = []
        seen: set[str] = set()
        for msg in recent_messages:
            role = str(msg.get("role", "")).strip().lower()
            speaker = str(msg.get("speaker", "")).strip()
            if role != "assistant" or not speaker:
                continue
            lowered = speaker.lower()
            if lowered in current_ai_names or lowered in seen:
                continue
            seen.add(lowered)
            other_ai_names.append(speaker)
        return other_ai_names

    @staticmethod
    def _summarize_recent_messages(recent_messages: list[dict[str, str]]) -> list[str]:
        context_lines: list[str] = []
        for msg in IntentAnalyzer._recent_distinct_turns(
            recent_messages,
            limit=_INTENT_CONTEXT_MESSAGE_LIMIT,
        ):
            role = str(msg.get("role", "")).strip().lower()
            speaker = str(msg.get("speaker", "")).strip()
            content = IntentAnalyzer._compact_text(
                str(msg.get("content", "")),
                max_chars=_INTENT_CONTEXT_TEXT_LIMIT,
            )
            if not content:
                continue
            label = speaker if speaker else role or "unknown"
            context_lines.append(f"[{label}] {content}")
        return context_lines

    @staticmethod
    def _recent_distinct_turns(
        recent_messages: list[dict[str, str]],
        *,
        limit: int,
    ) -> list[dict[str, str]]:
        collapsed: list[dict[str, str]] = []
        previous_key: tuple[str, str] | None = None
        for msg in recent_messages:
            role = str(msg.get("role", "")).strip().lower()
            speaker = str(msg.get("speaker", "")).strip()
            key = (role, speaker.lower())
            if key == previous_key:
                if collapsed:
                    collapsed[-1] = msg
                continue
            collapsed.append(msg)
            previous_key = key
        return collapsed[-limit:] if limit > 0 else collapsed

    @staticmethod
    def _extract_recent_speakers(
        *,
        recent_messages: list[dict[str, str]],
        role: str,
        limit: int,
    ) -> list[str]:
        speakers: list[str] = []
        seen: set[str] = set()
        for msg in reversed(IntentAnalyzer._recent_distinct_turns(recent_messages, limit=limit * 2)):
            msg_role = str(msg.get("role", "")).strip().lower()
            speaker = str(msg.get("speaker", "")).strip()
            if msg_role != role or not speaker:
                continue
            lowered = speaker.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            speakers.append(speaker)
            if len(speakers) >= limit:
                break
        return speakers

    @staticmethod
    def _compact_text(text: str, *, max_chars: int) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= max_chars:
            return compact
        return compact[:max_chars]

    @staticmethod
    def _name_variants(name: str) -> set[str]:
        normalized = re.sub(r"\s+", " ", str(name).strip())
        if not normalized:
            return set()

        variants: set[str] = set()
        lowered = normalized.lower()
        compact = normalized.replace(" ", "").lower()
        if len(lowered) >= 2:
            variants.add(lowered)
        if len(compact) >= 2:
            variants.add(compact)

        for part in re.split(r"[\s/|,，;；:：()（）\[\]<>《》]+", normalized):
            lowered_part = part.strip().lower()
            if len(lowered_part) >= 2:
                variants.add(lowered_part)

        return variants

    @staticmethod
    def _extract_name_hits(content: str, names: list[str]) -> list[str]:
        lowered_content = str(content).strip().lower()
        hits: list[str] = []
        for name in names:
            variants = IntentAnalyzer._name_variants(name)
            if any(variant in lowered_content for variant in variants):
                if name not in hits:
                    hits.append(name)
        return hits

    @staticmethod
    def _infer_pronoun_target_scope(
        *,
        recent_messages: list[dict[str, str]],
        current_ai_names: set[str],
        other_ai_names: set[str],
        human_names: set[str],
    ) -> str:
        distinct_turns = IntentAnalyzer._recent_distinct_turns(
            recent_messages,
            limit=_PRONOUN_CONTEXT_TURN_LIMIT,
        )
        if not distinct_turns:
            return "unknown"

        scores = {
            "self_ai": 0.0,
            "other_ai": 0.0,
            "human": 0.0,
        }
        recency_weights = (1.0, 0.7, 0.45, 0.3)
        for index, msg in enumerate(reversed(distinct_turns)):
            role = str(msg.get("role", "")).strip().lower()
            speaker = str(msg.get("speaker", "")).strip().lower()
            weight = recency_weights[index] if index < len(recency_weights) else 0.2
            if role == "assistant":
                if not speaker or speaker in current_ai_names:
                    scores["self_ai"] += weight * 1.25
                    continue
                if speaker in other_ai_names:
                    scores["other_ai"] += weight * 1.25
                    continue
                scores["other_ai"] += weight * 1.1
                continue
            if role == "user":
                if speaker in human_names or speaker:
                    scores["human"] += weight * 0.55

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_label, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if top_score <= 0:
            return "unknown"
        if second_score > 0 and top_score < second_score * 1.15:
            return "unknown"
        return top_label


__all__ = [
    "INTENT_TYPES",
    "TARGET_TYPES",
    "TARGET_SCOPES",
    "IntentAnalysis",
    "IntentAnalyzer",
]
