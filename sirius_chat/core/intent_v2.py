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
_AI_EVIDENCE_TOKENS = (
    "ai", "bot", "机器人", "智能体", "助手", "agent", "llm", "gpt", "claude", "gemini", "deepseek", "qwen", "chatgpt",
)

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
    "- target=ai 且 target_scope=self_ai：只有在消息明确点名当前助手名字/别名，或有非常强的上下文承接证据表明用户正在直接回应当前助手上一轮发言时，才可使用 self_ai\n"
    "- target=ai 且 target_scope=other_ai：消息明确指向群内其他 AI，或点名了名字上带明显 AI 线索的其他对象\n"
    "- target=others 且 target_scope=human：消息明确指向群内其他参与者，但现有证据更像人类对象\n"
    "- target=everyone：消息面向全体（公告、一般感叹、分享信息）\n"
    "- target=unknown：无法确定指向\n\n"
    "重要规则：\n"
    "- 仅凭\"你\"字不能判定指向AI，必须结合上下文确认\n"
    "- 群聊里可能有多个 AI，必须优先判断消息是指向当前助手，还是指向其他 AI\n"
    "- 判断 self_ai 时，当前助手名字/别名命中是最强依据；不要因为系统里存在当前助手就默认把 target=ai 等价成 self_ai\n"
    "- 不能预设群内其他对象天然是人类或 AI。除非对象名称、别称里含有 AI/BOT/机器人/智能体/助手/GPT/Claude/Qwen 等明确线索，否则应先把它视为\"可能为AI的对象\"，再结合上下文判定\n"
    "- 如果当前消息命中了其他对象名字，而没有命中当前助手名字/别名，通常不能判定为 self_ai\n"
    "- 如果只知道消息指向某个 AI，但不能可靠区分是当前助手还是其他 AI，target_scope 应优先返回 other_ai 或 unknown，不要勉强写 self_ai\n"
    "- 对没有明显 AI 线索的对象，不要因为它近期由 assistant 说过话就武断判成人类或 AI，要根据最近交互链、代词承接、内容风格一起判断\n"
    "- 群控/停用类命令（如关闭本群AI、禁用机器人、别让bot说话）如果没有明确点名当前助手，不应判定为 self_ai，也不应触发当前助手回复\n"
    "- 如果上一条消息是某个人说的，当前消息可能在回复那个人而非AI\n"
    "- 当群聊中有多人对话时，要根据话题连续性判断说话对象\n"
    "- 当证据不足时，宁可给出 other_ai 或 unknown，也不要轻易判成 self_ai\n"
    "- 任何明确带有机械指令风格的消息，如关闭本群AI、禁用机器人等，不认为是self_ai\n"
    "- 不要输出任何额外文字\n"
)

_INTENT_CONTEXT_MESSAGE_LIMIT = 4
_INTENT_CONTEXT_TEXT_LIMIT = 48
_PRONOUN_CONTEXT_TURN_LIMIT = 4
_IDENTITY_SUMMARY_LIMIT = 4
_ENVIRONMENT_CONTEXT_LIMIT = 120
_NAME_SPLIT_PATTERN = r"[\s/|,，;；:：()（）\[\]<>《》【】_\-]+"
_GENERIC_IDENTITY_TOKENS = frozenset({"ai", "bot", "agent", "机器人", "智能体", "BOT"})
_GROUP_CONTROL_ACTION_MARKERS = (
    "关闭", "关掉", "关了", "停用", "禁用", "禁言", "屏蔽", "移除", "踢出", "停止", "停掉", "别让", "不要让",
)
_GROUP_CONTROL_SCOPE_MARKERS = ("本群", "群里", "群内", "这个群", "该群", "全群")
_GROUP_CONTROL_AI_OBJECT_MARKERS = ("ai", "bot", "机器人", "助手", "智能体", "BOT")
_GROUP_CONTROL_TARGET_MARKERS = ("回复", "发言", "说话", "开口", "功能", "模式")


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
    force_no_reply: bool = False
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
        participant_alias_map: dict[str, list[str]] | None = None,
        recent_messages: list[dict[str, str]],
        environment_context: str = "",
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
            participant_alias_map: 群内人类参与者别称映射。
            recent_messages: 近期聊天历史 [{role, content, speaker?}]。
            environment_context: 外部注入的环境信息，如群名/群描述。
            call_provider: 异步 LLM 调用函数。
            model: 分析用模型名。
        """
        request = IntentAnalyzer.build_request(
            content=content,
            agent_name=agent_name,
            agent_alias=agent_alias,
            participant_names=participant_names,
            participant_alias_map=participant_alias_map,
            recent_messages=recent_messages,
            environment_context=environment_context,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            raw = await call_provider(request)
            parsed = IntentAnalyzer._parse_response(raw)
            if parsed is not None:
                return IntentAnalyzer.post_process_analysis(
                    parsed,
                    content=content,
                    agent_name=agent_name,
                    agent_alias=agent_alias,
                    participant_names=participant_names,
                    participant_alias_map=participant_alias_map,
                    recent_messages=recent_messages,
                )
            logger.warning("意图分析响应解析失败，使用回退。")
            return IntentAnalyzer.fallback_analysis(
                content=content,
                agent_name=agent_name,
                agent_alias=agent_alias,
                participant_names=participant_names,
                recent_messages=recent_messages,
                participant_alias_map=participant_alias_map,
            )
        except Exception as exc:
            logger.warning("意图分析 LLM 调用失败，使用回退: %s", exc)
            return IntentAnalyzer.fallback_analysis(
                content=content,
                agent_name=agent_name,
                agent_alias=agent_alias,
                participant_names=participant_names,
                recent_messages=recent_messages,
                participant_alias_map=participant_alias_map,
            )

    @staticmethod
    def build_request(
        *,
        content: str,
        agent_name: str,
        agent_alias: str,
        participant_names: list[str],
        participant_alias_map: dict[str, list[str]] | None = None,
        recent_messages: list[dict[str, str]],
        environment_context: str = "",
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 192,
    ) -> GenerationRequest:
        """Build a GenerationRequest for the intent analysis task."""

        current_ai_names = [name for name in (agent_name, agent_alias) if str(name).strip()]
        other_ai_identity_map = IntentAnalyzer._build_other_ai_identity_map(
            recent_messages=recent_messages,
            agent_name=agent_name,
            agent_alias=agent_alias,
        )
        participant_identity_map = IntentAnalyzer._build_human_identity_map(
            participant_names,
            participant_alias_map,
        )
        ai_evidence_identity_map = IntentAnalyzer._build_ai_evidence_identity_map(
            participant_names,
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        possible_ai_identity_map = IntentAnalyzer._build_possible_ai_identity_map(
            participant_names,
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        self_hits = IntentAnalyzer._extract_name_hits(content, current_ai_names)
        other_ai_hits = IntentAnalyzer._extract_identity_hits(content, other_ai_identity_map)
        ai_evidence_hits = IntentAnalyzer._extract_identity_hits(content, ai_evidence_identity_map)
        participant_hits = IntentAnalyzer._extract_identity_hits(content, participant_identity_map)
        possible_ai_hits = IntentAnalyzer._extract_identity_hits(content, possible_ai_identity_map)
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
        environment_info = IntentAnalyzer._format_environment_context(environment_context)

        participants_info = (
            "群内其它已知对象："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    name,
                    participant_identity_map.get(name, []),
                )
                for name in participant_names[:8]
            )
            if participant_names else ""
        )
        current_ai_info = f"当前助手名字/别名：{', '.join(current_ai_names)}" if current_ai_names else ""
        other_ai_info = (
            "近期其他AI发言者："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    speaker,
                    other_ai_identity_map.get(speaker, []),
                    alias_label="线索",
                )
                for speaker in list(other_ai_identity_map.keys())[:_IDENTITY_SUMMARY_LIMIT]
            )
            if other_ai_identity_map else ""
        )
        ai_evidence_info = (
            "名称上带明确AI线索的对象："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    speaker,
                    ai_evidence_identity_map.get(speaker, []),
                    alias_label="AI线索",
                )
                for speaker in list(ai_evidence_identity_map.keys())[:_IDENTITY_SUMMARY_LIMIT]
            )
            if ai_evidence_identity_map else ""
        )
        possible_ai_info = (
            "名称上暂无法确定、需结合上下文判断的对象："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    speaker,
                    possible_ai_identity_map.get(speaker, []),
                    alias_label="别称",
                )
                for speaker in list(possible_ai_identity_map.keys())[:_IDENTITY_SUMMARY_LIMIT]
            )
            if possible_ai_identity_map else ""
        )
        recent_ai_info = (
            "最近AI发言者（近到远）："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    speaker,
                    IntentAnalyzer._aliases_for_recent_ai_speaker(
                        speaker,
                        current_ai_names=current_ai_names,
                        other_ai_identity_map=other_ai_identity_map,
                    ),
                    alias_label="线索",
                )
                for speaker in recent_ai_speakers
            )
            if recent_ai_speakers else ""
        )
        recent_human_info = (
            "最近用户侧发言者（近到远）："
            + ", ".join(
                IntentAnalyzer._format_identity_summary(
                    speaker,
                    participant_identity_map.get(speaker, []),
                )
                for speaker in recent_human_speakers
            )
            if recent_human_speakers else ""
        )
        self_hit_info = f"当前消息命中的当前助手名字/别名：{', '.join(self_hits)}" if self_hits else ""
        other_ai_hit_info = f"当前消息命中的其他AI名字：{', '.join(other_ai_hits)}" if other_ai_hits else ""
        ai_evidence_hit_info = f"当前消息命中的名称含AI线索对象：{', '.join(ai_evidence_hits)}" if ai_evidence_hits else ""
        possible_ai_hit_info = f"当前消息命中的可能为AI对象：{', '.join(possible_ai_hits)}" if possible_ai_hits else ""
        participant_hit_info = f"当前消息命中的其它对象名字：{', '.join(participant_hits)}" if participant_hits else ""

        user_prompt = (
            f"当前助手名称: {agent_name}"
            + (f" (别名: {agent_alias})" if agent_alias else "")
            + (f"\n{current_ai_info}" if current_ai_info else "")
            + (f"\n{other_ai_info}" if other_ai_info else "")
            + (f"\n{ai_evidence_info}" if ai_evidence_info else "")
            + (f"\n{possible_ai_info}" if possible_ai_info else "")
            + (f"\n{participants_info}" if participants_info else "")
            + (f"\n{environment_info}" if environment_info else "")
            + (f"\n{recent_ai_info}" if recent_ai_info else "")
            + (f"\n{recent_human_info}" if recent_human_info else "")
            + (f"\n{self_hit_info}" if self_hit_info else "")
            + (f"\n{other_ai_hit_info}" if other_ai_hit_info else "")
            + (f"\n{ai_evidence_hit_info}" if ai_evidence_hit_info else "")
            + (f"\n{possible_ai_hit_info}" if possible_ai_hit_info else "")
            + (f"\n{participant_hit_info}" if participant_hit_info else "")
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
        participant_alias_map: dict[str, list[str]] | None = None,
    ) -> IntentAnalysis:
        """关键词快速回退分析（零 LLM 开销）。

        增强版：区分当前助手自身、其他 AI 与其他参与者。
        """
        text = content.strip().lower()
        original_text = content.strip()
        current_ai_names = [name for name in (agent_name, agent_alias) if str(name).strip()]
        other_ai_identity_map = IntentAnalyzer._build_other_ai_identity_map(
            recent_messages=recent_messages or [],
            agent_name=agent_name,
            agent_alias=agent_alias,
        )
        ai_evidence_identity_map = IntentAnalyzer._build_ai_evidence_identity_map(
            participant_names or [],
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        other_ai_names = list(other_ai_identity_map.keys())
        human_identity_map = IntentAnalyzer._build_human_identity_map(
            participant_names or [],
            participant_alias_map,
            exclude_names=set(ai_evidence_identity_map.keys()),
        )
        possible_ai_identity_map = IntentAnalyzer._build_possible_ai_identity_map(
            participant_names or [],
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        human_names = list(human_identity_map.keys())

        # ── 判断 target ──
        self_ai_hits = IntentAnalyzer._extract_name_hits(content, current_ai_names)
        other_ai_hits = IntentAnalyzer._extract_identity_hits(content, other_ai_identity_map)
        ai_evidence_hits = IntentAnalyzer._extract_identity_hits(content, ai_evidence_identity_map)
        other_human_hits = IntentAnalyzer._extract_identity_hits(content, human_identity_map)
        possible_ai_hits = IntentAnalyzer._extract_identity_hits(content, possible_ai_identity_map)
        self_ai_directed = bool(self_ai_hits)
        other_ai_directed = bool(other_ai_hits or ai_evidence_hits)
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
        elif (command_marker := IntentAnalyzer._find_group_control_action_marker(text)) is not None:
            intent_type = "command"
            reason = "消息包含明显的操作/控制类动词，判定为命令。"
            evidence_span = command_marker
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
            reason = f"消息更像是在对其他AI说话，而不是当前助手。{reason}"
        elif not directed_at_ai and target == "others":
            reason = f"消息指向其他参与者，非 AI 对话。{reason}"
        elif not evidence_span and original_text:
            evidence_span = original_text[:24]

        result = IntentAnalysis(
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
        return IntentAnalyzer.post_process_analysis(
            result,
            content=content,
            agent_name=agent_name,
            agent_alias=agent_alias,
            participant_names=participant_names or [],
            participant_alias_map=participant_alias_map,
            recent_messages=recent_messages or [],
        )

    @staticmethod
    def post_process_analysis(
        analysis: IntentAnalysis,
        *,
        content: str,
        agent_name: str,
        agent_alias: str,
        participant_names: list[str] | None = None,
        participant_alias_map: dict[str, list[str]] | None = None,
        recent_messages: list[dict[str, str]] | None = None,
    ) -> IntentAnalysis:
        current_ai_names = [name for name in (agent_name, agent_alias) if str(name).strip()]
        other_ai_identity_map = IntentAnalyzer._build_other_ai_identity_map(
            recent_messages=recent_messages or [],
            agent_name=agent_name,
            agent_alias=agent_alias,
        )
        ai_evidence_identity_map = IntentAnalyzer._build_ai_evidence_identity_map(
            participant_names or [],
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        human_identity_map = IntentAnalyzer._build_human_identity_map(
            participant_names or [],
            participant_alias_map,
            exclude_names=set(ai_evidence_identity_map.keys()),
        )
        possible_ai_identity_map = IntentAnalyzer._build_possible_ai_identity_map(
            participant_names or [],
            participant_alias_map,
            current_ai_names=current_ai_names,
            other_ai_identity_map=other_ai_identity_map,
        )
        self_ai_hits = IntentAnalyzer._extract_name_hits(content, current_ai_names)
        other_ai_hits = IntentAnalyzer._extract_identity_hits(content, other_ai_identity_map)
        ai_evidence_hits = IntentAnalyzer._extract_identity_hits(content, ai_evidence_identity_map)
        other_human_hits = IntentAnalyzer._extract_identity_hits(content, human_identity_map)
        possible_ai_hits = IntentAnalyzer._extract_identity_hits(content, possible_ai_identity_map)

        if IntentAnalyzer._looks_like_group_ai_control_command(content) and not self_ai_hits:
            analysis.force_no_reply = True
            analysis.intent_type = "command"
            if analysis.target_scope == "self_ai":
                if other_ai_hits or ai_evidence_hits:
                    analysis.target = "ai"
                    analysis.target_scope = "other_ai"
                elif other_human_hits:
                    analysis.target = "others"
                    analysis.target_scope = "human"
                else:
                    analysis.target = "unknown"
                    analysis.target_scope = "unknown"
            analysis.directed_at_ai = analysis.target == "ai"
            analysis.directed_at_current_ai = False
            analysis.importance = min(analysis.importance, 0.05)
            analysis.confidence = max(analysis.confidence, 0.7)
            analysis.reason = "消息是群控/停用类命令，未明确点名当前助手，不触发当前助手回复。"
            if not analysis.evidence_span:
                analysis.evidence_span = content.strip()[:24]

        if analysis.target_scope == "self_ai" and not self_ai_hits and (other_ai_hits or ai_evidence_hits):
            analysis.target = "ai"
            analysis.target_scope = "other_ai"
            analysis.directed_at_ai = True
            analysis.directed_at_current_ai = False
            analysis.importance = min(analysis.importance, 0.2)
            analysis.confidence = max(analysis.confidence, 0.7)
            analysis.reason = "消息命中了其他AI或名称上带明确AI线索的对象，且没有命中当前助手名字/别名，回退为 other_ai 以减少抢答。"
            if not analysis.evidence_span:
                analysis.evidence_span = (other_ai_hits or ai_evidence_hits)[0]

        if analysis.target_scope == "self_ai" and not self_ai_hits:
            inferred_scope = IntentAnalyzer._infer_pronoun_target_scope(
                recent_messages=recent_messages or [],
                current_ai_names={name.lower() for name in current_ai_names},
                other_ai_names={name.lower() for name in other_ai_identity_map.keys()},
                human_names={name.lower() for name in human_identity_map.keys()},
            )

            if other_human_hits:
                analysis.target = "others"
                analysis.target_scope = "human"
                analysis.directed_at_ai = False
                analysis.directed_at_current_ai = False
                analysis.importance = min(analysis.importance, 0.2)
                analysis.confidence = max(analysis.confidence, 0.7)
                analysis.reason = "消息没有命中当前助手名字/别名，却命中了其他参与者名字，不判定为 self_ai。"
                if not analysis.evidence_span:
                    analysis.evidence_span = other_human_hits[0]
            elif possible_ai_hits:
                analysis.target = "unknown"
                analysis.target_scope = "unknown"
                analysis.directed_at_ai = False
                analysis.directed_at_current_ai = False
                analysis.importance = min(analysis.importance, 0.2)
                analysis.confidence = max(analysis.confidence, 0.65)
                analysis.reason = "消息没有命中当前助手名字/别名，却命中了其他待判定对象，证据不足，不判定为 self_ai。"
                if not analysis.evidence_span:
                    analysis.evidence_span = possible_ai_hits[0]
            elif inferred_scope != "self_ai":
                analysis.target = "unknown"
                analysis.target_scope = "unknown"
                analysis.directed_at_ai = False
                analysis.directed_at_current_ai = False
                analysis.importance = min(analysis.importance, 0.2)
                analysis.confidence = max(analysis.confidence, 0.65)
                analysis.reason = "消息没有明确命中当前助手名字/别名，且上下文不足以稳定指向当前助手，不判定为 self_ai。"
                if not analysis.evidence_span:
                    analysis.evidence_span = content.strip()[:24]

        return analysis

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
                normalized_scope = "unknown"
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
    def _build_other_ai_identity_map(
        *,
        recent_messages: list[dict[str, str]],
        agent_name: str,
        agent_alias: str,
    ) -> dict[str, list[str]]:
        current_ai_names = {
            str(name).strip().lower()
            for name in (agent_name, agent_alias)
            if str(name).strip()
        }
        other_ai_names: dict[str, list[str]] = {}
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
            other_ai_names[speaker] = IntentAnalyzer._extract_alias_cues_from_speaker(speaker)
        return other_ai_names

    @staticmethod
    def _extract_other_ai_names(
        *,
        recent_messages: list[dict[str, str]],
        agent_name: str,
        agent_alias: str,
    ) -> list[str]:
        return list(IntentAnalyzer._build_other_ai_identity_map(
            recent_messages=recent_messages,
            agent_name=agent_name,
            agent_alias=agent_alias,
        ).keys())

    @staticmethod
    def _build_human_identity_map(
        participant_names: list[str],
        participant_alias_map: dict[str, list[str]] | None,
        exclude_names: set[str] | None = None,
    ) -> dict[str, list[str]]:
        alias_map = participant_alias_map or {}
        identity_map: dict[str, list[str]] = {}
        excluded = {name.strip().lower() for name in (exclude_names or set()) if str(name).strip()}
        for name in participant_names:
            normalized_name = str(name).strip()
            if not normalized_name:
                continue
            if normalized_name.lower() in excluded:
                continue
            aliases = [
                alias for alias in alias_map.get(normalized_name, [])
                if alias.strip() and alias.strip().lower() != normalized_name.lower()
            ]
            identity_map[normalized_name] = IntentAnalyzer._dedupe_names(aliases)
        return identity_map

    @staticmethod
    def _build_ai_evidence_identity_map(
        participant_names: list[str],
        participant_alias_map: dict[str, list[str]] | None,
        *,
        current_ai_names: list[str],
        other_ai_identity_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        alias_map = participant_alias_map or {}
        current_name_set = {name.strip().lower() for name in current_ai_names if str(name).strip()}
        known_other_ai_set = {name.strip().lower() for name in other_ai_identity_map.keys()}
        identity_map: dict[str, list[str]] = {}
        for name in participant_names:
            normalized_name = str(name).strip()
            if not normalized_name:
                continue
            lowered = normalized_name.lower()
            if lowered in current_name_set or lowered in known_other_ai_set:
                continue
            raw_aliases = [alias for alias in alias_map.get(normalized_name, []) if alias.strip()]
            evidence = IntentAnalyzer._extract_ai_evidence_terms([normalized_name, *raw_aliases])
            if evidence:
                identity_map[normalized_name] = evidence
        return identity_map

    @staticmethod
    def _build_possible_ai_identity_map(
        participant_names: list[str],
        participant_alias_map: dict[str, list[str]] | None,
        *,
        current_ai_names: list[str],
        other_ai_identity_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        alias_map = participant_alias_map or {}
        current_name_set = {name.strip().lower() for name in current_ai_names if str(name).strip()}
        known_other_ai_set = {name.strip().lower() for name in other_ai_identity_map.keys()}
        identity_map: dict[str, list[str]] = {}
        for name in participant_names:
            normalized_name = str(name).strip()
            if not normalized_name:
                continue
            lowered = normalized_name.lower()
            if lowered in current_name_set or lowered in known_other_ai_set:
                continue
            raw_aliases = [alias for alias in alias_map.get(normalized_name, []) if alias.strip()]
            evidence = IntentAnalyzer._extract_ai_evidence_terms([normalized_name, *raw_aliases])
            if evidence:
                continue
            candidates = IntentAnalyzer._dedupe_names(raw_aliases)
            if candidates:
                identity_map[normalized_name] = candidates
            else:
                identity_map[normalized_name] = [normalized_name]
        return identity_map

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
    def _format_environment_context(environment_context: str) -> str:
        compact = IntentAnalyzer._compact_text(environment_context, max_chars=_ENVIRONMENT_CONTEXT_LIMIT)
        return f"环境线索：{compact}" if compact else ""

    @staticmethod
    def _format_identity_summary(name: str, aliases: list[str], *, alias_label: str = "别称") -> str:
        normalized_name = str(name).strip()
        if not normalized_name:
            return ""
        unique_aliases = [
            alias for alias in IntentAnalyzer._dedupe_names(aliases)
            if alias.strip().lower() != normalized_name.lower()
        ]
        if not unique_aliases:
            return normalized_name
        return f"{normalized_name} ({alias_label}: {', '.join(unique_aliases[:3])})"

    @staticmethod
    def _aliases_for_recent_ai_speaker(
        speaker: str,
        *,
        current_ai_names: list[str],
        other_ai_identity_map: dict[str, list[str]],
    ) -> list[str]:
        lowered = speaker.strip().lower()
        current_aliases = [
            name for name in current_ai_names
            if name.strip() and name.strip().lower() != lowered
        ]
        if current_aliases:
            return current_aliases
        return other_ai_identity_map.get(speaker, [])

    @staticmethod
    def _dedupe_names(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(normalized)
        return result

    @staticmethod
    def _extract_alias_cues_from_speaker(name: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", str(name).strip())
        if not normalized:
            return []

        cues: list[str] = []
        seen = {normalized.lower()}
        for part in re.split(_NAME_SPLIT_PATTERN, normalized):
            value = part.strip()
            lowered = value.lower()
            if len(value) < 2 or lowered in seen or lowered in _GENERIC_IDENTITY_TOKENS:
                continue
            seen.add(lowered)
            cues.append(value)

        if IntentAnalyzer._contains_marker(normalized.lower(), "ai") and "ai" not in seen:
            cues.append("AI")
            seen.add("ai")
        if IntentAnalyzer._contains_marker(normalized.lower(), "bot") and "bot" not in seen:
            cues.append("Bot")
            seen.add("bot")
        if "机器人" in normalized and "机器人" not in seen:
            cues.append("机器人")
            seen.add("机器人")
        return cues[:4]

    @staticmethod
    def _extract_ai_evidence_terms(values: list[str]) -> list[str]:
        evidence: list[str] = []
        seen: set[str] = set()
        for value in values:
            lowered = str(value).strip().lower()
            if not lowered:
                continue
            for token in _AI_EVIDENCE_TOKENS:
                token_lower = token.lower()
                if token_lower in seen:
                    continue
                if IntentAnalyzer._contains_marker(lowered, token_lower):
                    seen.add(token_lower)
                    evidence.append(token)
        return evidence[:4]

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

        for part in re.split(_NAME_SPLIT_PATTERN, normalized):
            lowered_part = part.strip().lower()
            if len(lowered_part) >= 2 and lowered_part not in _GENERIC_IDENTITY_TOKENS:
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
    def _extract_identity_hits(content: str, identity_map: dict[str, list[str]]) -> list[str]:
        lowered_content = str(content).strip().lower()
        hits: list[str] = []
        for display_name, aliases in identity_map.items():
            variants: set[str] = set()
            for candidate in [display_name, *aliases]:
                variants.update(IntentAnalyzer._name_variants(candidate))
            if any(variant in lowered_content for variant in variants):
                hits.append(display_name)
        return hits

    @staticmethod
    def _contains_marker(text: str, marker: str) -> bool:
        if marker in {"ai", "bot", "agent"}:
            return re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", text) is not None
        return marker in text

    @staticmethod
    def _find_group_control_action_marker(text: str) -> str | None:
        for marker in _GROUP_CONTROL_ACTION_MARKERS:
            if marker in text:
                return marker
        return None

    @staticmethod
    def _looks_like_group_ai_control_command(content: str) -> bool:
        text = str(content).strip().lower()
        if not text:
            return False
        has_action = IntentAnalyzer._find_group_control_action_marker(text) is not None
        if not has_action:
            return False
        has_scope = any(marker in text for marker in _GROUP_CONTROL_SCOPE_MARKERS)
        has_ai_object = any(IntentAnalyzer._contains_marker(text, marker) for marker in _GROUP_CONTROL_AI_OBJECT_MARKERS)
        has_control_target = any(marker in text for marker in _GROUP_CONTROL_TARGET_MARKERS)
        return has_ai_object and (has_scope or has_control_target)

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
