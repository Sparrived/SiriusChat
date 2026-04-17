"""Response assembler: prompt construction and style adaptation for v0.28+.

Implements the execution-layer components from the paper §5.4:
- ResponseAssembler / EmpathyGenerator: inject emotion context, empathy strategy,
  memory references, and group-level style into the LLM prompt.
- StyleAdapter: dynamically adjust max_tokens, temperature, and tone based on
  rhythm (heat/pace) and user communication preferences.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sirius_chat.models.emotion import AssistantEmotionState, EmotionState, EmpathyStrategy
from sirius_chat.models.intent_v3 import IntentAnalysisV3
from sirius_chat.models.models import Message
from sirius_chat.models.persona import PersonaProfile
from sirius_chat.memory.semantic.models import GroupSemanticProfile, UserSemanticProfile


# ---------------------------------------------------------------------------
# Style adaptation
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StyleParams:
    """Adapted style parameters for a single response generation."""

    max_tokens: int
    temperature: float
    tone_instruction: str
    length_instruction: str


class StyleAdapter:
    """Adapts response length and tone based on rhythm, heat, and user preferences."""

    # Token caps by heat level (paper §5.4.2)
    _HEAT_LIMITS: dict[str, int] = {
        "cold": 256,
        "warm": 128,
        "hot": 80,
        "overheated": 50,
    }

    # Token caps by conversation pace
    _PACE_LIMITS: dict[str, int] = {
        "accelerating": 80,
        "steady": 128,
        "decelerating": 200,
        "silent": 256,
    }

    def adapt(
        self,
        *,
        heat_level: str,
        pace: str,
        user_communication_style: str = "",
        topic_stability: float = 0.5,
        persona: PersonaProfile | None = None,
    ) -> StyleParams:
        """Compute style parameters for the current response context."""
        # Base limit = most restrictive of heat and pace
        base_limit = min(
            self._HEAT_LIMITS.get(heat_level, 128),
            self._PACE_LIMITS.get(pace, 128),
        )

        # Cold + stable topic → allow more detailed replies
        if heat_level == "cold" and topic_stability > 0.7:
            base_limit = min(400, int(base_limit * 1.5))

        max_tokens = base_limit
        temperature = 0.7
        tone_instruction = "保持自然友好"
        length_instruction = ""

        # Persona style override (highest priority)
        if persona:
            if persona.max_tokens_preference:
                max_tokens = min(max_tokens, persona.max_tokens_preference)
            if persona.temperature_preference:
                temperature = persona.temperature_preference
            if persona.communication_style:
                style = persona.communication_style.strip().lower()
                if style == "concise":
                    max_tokens = min(max_tokens, 80)
                    length_instruction = "请用1-2句话简洁回复。"
                elif style == "detailed":
                    length_instruction = "可以给出较详细的解释。"
                elif style == "formal":
                    tone_instruction = "保持礼貌正式的语气"
                elif style == "casual":
                    tone_instruction = "保持轻松随意的语气，可以用表情"
                elif style == "humorous":
                    tone_instruction = "保持幽默风趣的语气"
                # Persona-specific tone overrides generic
                if persona.humor_style:
                    tone_instruction += f"，{persona.humor_style}式幽默"
                if persona.emoji_preference == "heavy":
                    tone_instruction += "，多用表情包和emoji"
                elif persona.emoji_preference == "none":
                    tone_instruction += "，不用表情包"

        # User style override (second priority, if no persona or persona has no style)
        if not persona or not persona.communication_style:
            style = (user_communication_style or "").strip().lower()
            if style == "concise":
                max_tokens = min(max_tokens, 80)
                length_instruction = "请用1-2句话简洁回复。"
                temperature = 0.5
            elif style == "detailed":
                length_instruction = "可以给出较详细的解释。"
                temperature = 0.7
            elif style == "formal":
                tone_instruction = "保持礼貌正式的语气"
                temperature = 0.5
            elif style == "casual":
                tone_instruction = "保持轻松随意的语气，可以用表情"
                temperature = 0.8

        return StyleParams(
            max_tokens=max_tokens,
            temperature=temperature,
            tone_instruction=tone_instruction,
            length_instruction=length_instruction,
        )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

class ResponseAssembler:
    """Assembles LLM prompts with emotion, empathy, memory, and group context."""

    def __init__(self, style_adapter: StyleAdapter | None = None, persona: PersonaProfile | None = None) -> None:
        self.style_adapter = style_adapter or StyleAdapter()
        self.persona = persona

    def assemble(
        self,
        *,
        message: Message,
        intent: IntentAnalysisV3,
        emotion: EmotionState,
        empathy_strategy: EmpathyStrategy,
        memories: list[dict[str, Any]],
        group_profile: GroupSemanticProfile | None,
        user_profile: UserSemanticProfile | None,
        assistant_emotion: AssistantEmotionState,
        style_params: StyleParams | None = None,
        heat_level: str = "warm",
        pace: str = "steady",
        topic_stability: float = 0.5,
    ) -> str:
        """Build a complete prompt string for response generation.

        Sections (in order):
        1. System identity (persona-driven)
        2. Emotional context summary
        3. Empathy strategy instruction
        4. Relevant memory references
        5. Group style parameters + persona style
        6. User message
        """
        if style_params is None:
            style_params = self.style_adapter.adapt(
                heat_level=heat_level,
                pace=pace,
                user_communication_style=getattr(user_profile, "communication_style", ""),
                topic_stability=topic_stability,
                persona=self.persona,
            )

        sections: list[str] = []

        # 1. Role script (persona-driven narrative brief + scene anchor)
        if self.persona:
            sections.append(self.persona.build_system_prompt())
        else:
            sections.append(
                "[场景定位]\n"
                "你在一个多人聊天场景里。看到消息时，按自己的性格和情绪决定是否回应。\n"
                "回应时用自然口语，短句优先，不解释、不总结、不机械关怀。"
            )

        # 2. Emotional context
        sections.append(
            self._build_emotion_context(emotion, assistant_emotion, group_profile)
        )

        # 3. Empathy strategy (persona-aware)
        sections.append(self._build_empathy_instruction(empathy_strategy))

        # 4. Memory references
        if memories:
            sections.append(self._build_memory_context(memories))

        # 5. Group style + persona style
        if group_profile:
            sections.append(self._build_group_style(group_profile, style_params))
        else:
            sections.append(self._build_style_fallback(style_params))

        # 6. User message
        sections.append(f"[消息] {message.content}")

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_emotion_context(
        user_emotion: EmotionState,
        assistant_emotion: AssistantEmotionState,
        group_profile: GroupSemanticProfile | None,
    ) -> str:
        lines = ["[当下的感觉]"]

        basic = user_emotion.basic_emotion.name if user_emotion.basic_emotion else "平静"
        lines.append(
            f"对方现在大概{basic}"
            f"（愉悦度{user_emotion.valence:.1f}，"
            f"紧张度{user_emotion.arousal:.1f}，"
            f"强烈程度{user_emotion.intensity:.1f}）"
        )

        # Group atmosphere from latest snapshot
        group_valence = 0.0
        if group_profile and group_profile.atmosphere_history:
            group_valence = group_profile.atmosphere_history[-1].group_valence
        mood_desc = (
            "挺热络" if group_valence > 0.2
            else "有点低沉" if group_valence < -0.2
            else "一般"
        )
        lines.append(f"群里氛围{mood_desc}（群体愉悦度{group_valence:.1f}）")

        lines.append(
            f"你现在的感觉："
            f"愉悦度{assistant_emotion.valence:.1f}，"
            f"紧张度{assistant_emotion.arousal:.1f}"
        )
        return "\n".join(lines)

    @staticmethod
    def _build_empathy_instruction(strategy: EmpathyStrategy) -> str:
        lines = ["[共情策略]"]

        type_desc = {
            "confirm_action": "情感确认 → 先确认对方感受，再提供行动建议",
            "cognitive": "认知共情 → 帮助对方重新理解情境",
            "action": "行动支持 → 提供具体可行的帮助",
            "share_joy": "分享喜悦 → 积极回应，放大正面情绪",
            "presence": "陪伴存在 → 安静陪伴，不过度干预",
        }.get(strategy.strategy_type, strategy.strategy_type)

        lines.append(f"类型：{strategy.strategy_type} | 深度：level {strategy.depth_level}")
        lines.append(f"要求：{type_desc}")

        if strategy.personalization_params:
            for k, v in strategy.personalization_params.items():
                lines.append(f"  {k}：{v}")

        return "\n".join(lines)

    @staticmethod
    def _build_memory_context(memories: list[dict[str, Any]]) -> str:
        lines = ["[相关记忆]"]
        for m in memories[:3]:
            source = m.get("source", "memory")
            content = m.get("content", "")
            lines.append(f"- [{source}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_group_style(
        group_profile: GroupSemanticProfile,
        style_params: StyleParams,
    ) -> str:
        lines = ["[群体风格]"]

        style = group_profile.typical_interaction_style or "balanced"
        style_desc = {
            "humorous": "轻松幽默",
            "formal": "正式严谨",
            "balanced": "自然平衡",
        }.get(style, style)
        lines.append(f"群体典型风格：{style_desc}")
        lines.append(f"回复长度限制：{style_params.max_tokens} tokens")

        if style_params.length_instruction:
            lines.append(f"长度要求：{style_params.length_instruction}")
        if style_params.tone_instruction:
            lines.append(f"语气要求：{style_params.tone_instruction}")

        return "\n".join(lines)

    @staticmethod
    def _build_style_fallback(style_params: StyleParams) -> str:
        lines = ["[回复风格]"]
        lines.append(f"回复长度限制：{style_params.max_tokens} tokens")
        if style_params.length_instruction:
            lines.append(f"长度要求：{style_params.length_instruction}")
        if style_params.tone_instruction:
            lines.append(f"语气要求：{style_params.tone_instruction}")
        return "\n".join(lines)

    @staticmethod
    def _build_persona_context(persona: PersonaProfile) -> str:
        """Build persona-specific behavioral instructions."""
        lines: list[str] = ["[角色行为指引]"]

        if persona.catchphrases:
            cp = "，".join(f'"{c}"' for c in persona.catchphrases[:3])
            lines.append(f"你偶尔会说：{cp}")

        if persona.boundaries:
            bounds = "；".join(persona.boundaries[:3])
            lines.append(f"行为边界：{bounds}")

        if persona.taboo_topics:
            taboos = "、".join(persona.taboo_topics[:3])
            lines.append(f"避免谈论：{taboos}")

        if persona.preferred_topics:
            topics = "、".join(persona.preferred_topics[:3])
            lines.append(f"擅长话题：{topics}")

        if persona.stress_response:
            lines.append(f"压力下你会：{persona.stress_response}")

        if not lines[1:]:
            return ""
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Convenience helpers for non-immediate strategies
    # ------------------------------------------------------------------

    def assemble_delayed(
        self,
        *,
        message_content: str,
        group_profile: GroupSemanticProfile | None,
        style_params: StyleParams | None = None,
        heat_level: str = "warm",
        pace: str = "steady",
    ) -> str:
        """Build prompt for a delayed response (topic-gap trigger)."""
        if style_params is None:
            style_params = self.style_adapter.adapt(
                heat_level=heat_level, pace=pace, persona=self.persona
            )
        identity = (
            self.persona.build_system_prompt() if self.persona
            else "[场景定位]\n你在一个多人聊天场景里。"
        )
        sections = [
            identity,
            "[当前场景] 群里的话题有了自然间隙，你决定插一句。",
        ]
        if group_profile:
            style = group_profile.typical_interaction_style or "balanced"
            style_desc = {"humorous": "轻松幽默", "formal": "正式严谨", "balanced": "自然平衡"}.get(style, style)
            sections.append(f"[群体风格] {style_desc}")
        sections.append(f"[长度要求] {style_params.length_instruction or '保持简洁自然'}")
        sections.append(f"[消息] {message_content}")
        return "\n\n".join(sections)

    def assemble_proactive(
        self,
        *,
        trigger_reason: str,
        group_profile: GroupSemanticProfile | None,
        suggested_tone: str = "casual",
    ) -> str:
        """Build prompt for proactive initiation."""
        sections = [
            "[场景定位]\n你在一个多人聊天场景里。",
            "[当前场景] 群里一段时间没人说话，你决定开口说点什么。",
            f"[触发原因] {trigger_reason}",
            f"[语气] {suggested_tone}",
        ]
        if group_profile and group_profile.interest_topics:
            topics = ", ".join(group_profile.interest_topics[:3])
            sections.append(f"[群体兴趣] {topics}")
        return "\n\n".join(sections)
