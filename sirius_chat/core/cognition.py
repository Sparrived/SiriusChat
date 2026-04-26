"""Unified cognition analyzer: joint emotion + intent inference.

Philosophy alignment (v0.28+):
    Perceiving others' feelings and understanding their intent are two
    sides of the same cognitive act. We analyze them jointly:

    - Rule engine covers ~90% of cases at zero LLM cost.
    - Single LLM fallback covers the remaining ~10% with one cheap call.
    - Emotion flows naturally into intent scoring without async boundary.

"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from sirius_chat.models.emotion import BasicEmotion, EmotionState, EmpathyStrategy
from sirius_chat.models.intent_v3 import (
    EmotionalSubtype,
    HelpSubtype,
    IntentAnalysisV3,
    SilentSubtype,
    SocialIntent,
    SocialSubtype,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Emotion rule engine
# ------------------------------------------------------------------

_DEFAULT_LEXICON: dict[str, float] = {
    # Positive
    "开心": 0.8,
    "高兴": 0.9,
    "快乐": 0.85,
    "棒": 0.8,
    "好": 0.6,
    "喜欢": 0.7,
    "爱": 0.9,
    "感动": 0.7,
    "欣慰": 0.6,
    "满足": 0.7,
    "期待": 0.5,
    "兴奋": 0.85,
    "激动": 0.8,
    "惊喜": 0.7,
    "感谢": 0.6,
    "哈哈": 0.5,
    "嘿嘿": 0.4,
    "yyds": 0.9,
    "xswl": 0.8,
    "awsl": 0.7,
    "绝绝子": 0.7,
    "赞": 0.7,
    "牛逼": 0.7,
    "太棒了": 0.8,
    # Negative
    "难过": -0.7,
    "伤心": -0.8,
    "悲伤": -0.85,
    "痛苦": -0.9,
    "生气": -0.6,
    "愤怒": -0.8,
    "恼火": -0.5,
    "烦": -0.5,
    "讨厌": -0.6,
    "恶心": -0.7,
    "厌恶": -0.6,
    "失望": -0.6,
    "害怕": -0.7,
    "担心": -0.5,
    "焦虑": -0.6,
    "紧张": -0.5,
    "累": -0.4,
    "疲惫": -0.5,
    "绝望": -0.9,
    "崩溃": -0.9,
    "无语": -0.3,
    "郁闷": -0.5,
    "emo": -0.6,
    "蚌埠住了": -0.3,
    "呜呜": -0.6,
    "泪目": -0.4,
    "扎心": -0.5,
    "难受": -0.6,
    # Ambiguous / context-dependent
    "确实": 0.0,
    "好吧": -0.1,
    "哦": 0.0,
    "嗯": 0.0,
}

# ------------------------------------------------------------------
# Intent rule engine
# ------------------------------------------------------------------

_HELP_PATTERNS = [
    r"怎么\s*\S+",
    r"如何\s*\S+",
    r"为什么\s*\S+",
    r"有人.*吗",
    r"求助",
    r"请教",
    r"大佬",
    r"救命",
    r"报错",
    r"错误",
    r"exception",
    r"error",
    r"failed",
]

_EMOTIONAL_INDICATORS = [
    "感觉",
    "觉得",
    "心情",
    "难受",
    "开心",
    "难过",
    "累",
    "烦",
    "郁闷",
    "兴奋",
    "sad",
    "happy",
    "upset",
    "excited",
    "tired",
    "孤独",
    "寂寞",
    "压力",
]

_SOCIAL_INDICATORS = [
    "大家觉得",
    "有没有人",
    "一起",
    "推荐",
    "分享",
    "讨论",
    "聊聊",
    "怎么样",
    "如何看",
]

_URGENCY_KEYWORDS = {
    "high": {
        "崩溃",
        "救命",
        "急",
        "马上",
        "立刻",
        "现在",
        "死了",
        "完了",
        "urgent",
        "emergency",
        "asap",
        "help",
        "broken",
        "crash",
    },
    "medium": {
        "求助",
        "请问",
        "怎么",
        "如何",
        "为什么",
        "不懂",
        "不会",
        "confused",
        "stuck",
        "problem",
        "issue",
        "question",
    },
    "low": {
        "想问问",
        "好奇",
        "了解一下",
        "有空的话",
        "方便时",
        "wondering",
        "curious",
        "when you have time",
    },
}

# ------------------------------------------------------------------
# Joint LLM fallback prompt
# ------------------------------------------------------------------

# 主观题/观点询问关键词 —— 被点名时出现这些词应触发 IMMEDIATE
_SUBJECTIVE_KEYWORDS: tuple[str, ...] = (
    "你觉得", "你认为", "你怎么看", "你的看法", "你喜欢",
    "你觉得呢", "你觉得怎么样", "你的意见", "你觉得如何",
    "你更喜欢", "你最", "你讨厌", "你不喜欢", "你觉得好",
    "你怎么看",
)

# 需要上下文才能正确理解的短消息模式 —— 单独看像 filler，但有上下文时应视为对话延续
_CONTEXT_DEPENDENT_PATTERNS: tuple[str, ...] = (
    "为什么", "怎么回事", "真的吗", "那怎么办", "怎么办呢",
    "然后呢", "后来呢", "什么意思", "怎么说", "不会吧",
    "这样啊", "原来如此", "懂了", "这样吗", "那行", "好吧",
    "哦", "嗯嗯", "对对", "确实", "可以", "好的", "行吧",
)

_LLM_COGNITION_PROMPT = """分析以下消息的【情感状态】和【社交意图】。

{ai_identity}消息：{message}

要求输出 JSON：
{{
  "valence": -1.0 到 1.0（愉悦度，负值负面，正值正面）,
  "arousal": 0.0 到 1.0（唤醒度，0平静，1激动）,
  "intensity": 0.0 到 1.0（情感强度）,
  "basic_emotion": "joy|anger|sadness|anxiety|loneliness|neutral",
  "social_intent": "help_seeking|emotional|social|silent",
  "intent_subtype": "tech_help|info_query|venting|seeking_empathy|topic_discussion|filler",
  "urgency_score": 0-100,
  "relevance_score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "search_query": "用于检索记忆的一句话查询，概括用户核心需求（不是标签，是自然语言）"
}}

定义：
- help_seeking: 求助、提问、报错
- emotional: 表达情绪、寻求安慰
- social: 闲聊、讨论、分享
- silent: 无意义 filler（哈哈、确实、+1）
{ai_identity_note}
只输出 JSON，不要其他内容。"""


class CognitionAnalyzer:
    """Joint emotion + intent analyzer with unified rule engine and single LLM fallback.

    Replaces the sequential EmotionAnalyzer → IntentAnalyzerV3 pipeline with:
        1. Parallel rule-based emotion + intent scoring (zero cost)
        2. Single joint LLM fallback when either score is low-confidence
        3. Shared context fusion (trajectory + group sentiment)
        4. Unified empathy strategy selection
    """

    def __init__(
        self,
        lexicon: dict[str, float] | None = None,
        provider_async: Any | None = None,
        model_name: str = "gpt-4o-mini",
        ai_name: str = "",
        ai_aliases: list[str] | None = None,
        persona: Any | None = None,
    ) -> None:
        self.lexicon = lexicon or dict(_DEFAULT_LEXICON)
        self.provider_async = provider_async
        self.model_name = model_name
        self.ai_name = ai_name
        self.ai_aliases = [a.lower() for a in (ai_aliases or []) if a]
        self.persona = persona

        # Emotion state tracking
        self.trajectories: dict[str, list[tuple[str, EmotionState]]] = {}
        self.group_cache: dict[str, EmotionState] = {}
        self.empathy_prefs: dict[str, dict[str, Any]] = {}

        # Intent state tracking
        self.group_activity_history: dict[str, list[tuple[float, float]]] = {}
        self.user_response_prefs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        message: str,
        user_id: str,
        group_id: str | None = None,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[EmotionState, IntentAnalysisV3, EmpathyStrategy]:
        """Joint analysis: emotion, intent, and empathy strategy in one pass.

        Returns:
            (emotion_state, intent_analysis, empathy_strategy)
        """
        # 1. Rule-based emotion analysis
        text_emotion = self._text_analysis(message)

        # 2. Rule-based intent classification (fallback only)
        social_intent, subtype, intent_confidence = self._classify_intent(
            message, context_messages
        )
        search_query = message  # fallback when no LLM or LLM fails

        # 3. Intent analysis is mandatory via LLM when provider is available.
        #    LLM failure → default SILENT (safe degradation).
        llm_urgency: float | None = None
        llm_relevance: float | None = None
        if self.provider_async is not None:
            try:
                llm_result = await self._llm_cognition(message, context_messages)
                if llm_result is not None:
                    social_intent = llm_result["social_intent"]
                    subtype = llm_result["subtype"]
                    intent_confidence = llm_result.get("confidence", 0.85)
                    llm_urgency = llm_result.get("urgency_score")
                    llm_relevance = llm_result.get("relevance_score")
                    search_query = llm_result.get("search_query", message)
                    if text_emotion.confidence < 0.6:
                        text_emotion = llm_result["emotion"]
                else:
                    # LLM parse failure → safe SILENT
                    social_intent = SocialIntent.SILENT
                    subtype = SilentSubtype.IRRELEVANT
                    intent_confidence = 0.3
            except Exception as exc:
                logger.warning("LLM cognition failed: %s", exc)
                social_intent = SocialIntent.SILENT
                subtype = SilentSubtype.IRRELEVANT
                intent_confidence = 0.3

        # 4. Emotion context fusion
        context_emotion = self._context_inference(user_id)
        group_emotion = self.group_cache.get(group_id) if group_id else None
        emotion = self._fuse_emotion(text_emotion, context_emotion, group_emotion)
        self._update_trajectory(user_id, emotion)

        # 5. Intent scoring (emotion now available without async hop)
        urgency = self._calculate_urgency(
            message, user_id, group_id, emotion, context_messages
        )
        relevance = self._calculate_relevance(message, social_intent, user_id, group_id)
        # Prefer LLM's urgency/relevance when available (more context-aware)
        if llm_urgency is not None and llm_urgency > 0:
            urgency = llm_urgency
        if llm_relevance is not None and llm_relevance > 0:
            relevance = llm_relevance
        threshold = self._dynamic_threshold(group_id or "", user_id)
        strategy, priority, response_time = self._decide_strategy(
            social_intent, urgency, relevance, threshold
        )

        # Detect if message directly addresses the current AI
        directed = self._detect_directed_at_ai(message)
        if directed:
            # If explicitly addressed, never treat as silent filler
            if social_intent == SocialIntent.SILENT:
                social_intent = SocialIntent.SOCIAL
                subtype = SocialSubtype.TOPIC_DISCUSSION if subtype == SilentSubtype.FILLER else subtype

            # Determine if this is a question / subjective inquiry that requires an answer
            is_question = "?" in message or "？" in message
            is_subjective = any(kw in message for kw in _SUBJECTIVE_KEYWORDS)

            if is_question or is_subjective:
                # Direct question → immediate response (polite + functional)
                urgency = max(urgency, 80.0)
                relevance = max(relevance, 0.75)
            else:
                # Greeting or casual mention → prompt but not as urgent
                urgency = max(urgency, 70.0)
                relevance = max(relevance, 0.65)

        intent = IntentAnalysisV3(
            intent_type=self._intent_type_from_social(social_intent, message),
            social_intent=social_intent,
            intent_subtype=subtype.value,
            urgency_score=urgency,
            relevance_score=relevance,
            confidence=intent_confidence,
            response_priority=priority,
            estimated_response_time=response_time,
            search_query=search_query,
            threshold=threshold,
            directed_at_current_ai=directed,
        )

        # 6. Empathy strategy
        empathy = self.select_empathy_strategy(emotion, user_id)

        return emotion, intent, empathy

    def select_empathy_strategy(
        self,
        emotion: EmotionState,
        user_id: str,
    ) -> EmpathyStrategy:
        """Select empathy strategy based on emotion state."""
        user_pref = self.empathy_prefs.get(user_id, {})

        if emotion.valence < -0.5 and emotion.arousal > 0.7:
            strategy_type = "confirm_action"
            priority = 1
            depth = 3
        elif emotion.valence < -0.3:
            strategy_type = "cognitive"
            priority = 2
            depth = 2
        elif emotion.valence > 0.5:
            strategy_type = "share_joy"
            priority = 3
            depth = 2
        else:
            strategy_type = "presence"
            priority = 4
            depth = 1

        if user_pref.get("prefer_direct") and strategy_type == "cognitive":
            strategy_type = "action"

        return EmpathyStrategy(
            strategy_type=strategy_type,
            priority=priority,
            depth_level=depth,
            personalization_params=user_pref,
        )

    # ------------------------------------------------------------------
    # Group sentiment
    # ------------------------------------------------------------------

    def update_group_sentiment(
        self,
        group_id: str,
        emotion: EmotionState,
    ) -> None:
        """Update group sentiment cache with exponential moving average."""
        existing = self.group_cache.get(group_id)
        if existing is None:
            self.group_cache[group_id] = EmotionState(
                valence=emotion.valence,
                arousal=emotion.arousal,
                intensity=emotion.intensity,
                confidence=0.5,
            )
        else:
            alpha = 0.3
            self.group_cache[group_id] = EmotionState(
                valence=existing.valence * (1 - alpha) + emotion.valence * alpha,
                arousal=existing.arousal * (1 - alpha) + emotion.arousal * alpha,
                intensity=existing.intensity * (1 - alpha) + emotion.intensity * alpha,
                confidence=min(1.0, existing.confidence + 0.05),
            )

    def detect_emotion_islands(
        self,
        group_id: str,
        recent_emotions: dict[str, EmotionState],
    ) -> list[dict[str, Any]]:
        """Detect users whose emotion deviates significantly from group mean."""
        if not recent_emotions or len(recent_emotions) < 2:
            return []

        group = self.group_cache.get(group_id)
        if group is None:
            valences = [e.valence for e in recent_emotions.values()]
            arousals = [e.arousal for e in recent_emotions.values()]
            group = EmotionState(
                valence=sum(valences) / len(valences),
                arousal=sum(arousals) / len(arousals),
                intensity=0.5,
                confidence=0.5,
            )

        valences = [e.valence for e in recent_emotions.values()]
        mean_v = sum(valences) / len(valences)
        std_v = (
            math.sqrt(sum((v - mean_v) ** 2 for v in valences) / len(valences))
            if len(valences) > 1
            else 0.0
        )

        arousals = [e.arousal for e in recent_emotions.values()]
        mean_a = sum(arousals) / len(arousals)
        std_a = (
            math.sqrt(sum((a - mean_a) ** 2 for a in arousals) / len(arousals))
            if len(arousals) > 1
            else 0.0
        )

        islands = []
        for uid, emotion in recent_emotions.items():
            dev_v = abs(emotion.valence - group.valence)
            dev_a = abs(emotion.arousal - group.arousal)
            z_v = dev_v / std_v if std_v > 0.01 else dev_v * 2
            z_a = dev_a / std_a if std_a > 0.01 else dev_a * 2
            if z_v > 1.5 or z_a > 1.5:
                islands.append(
                    {
                        "user_id": uid,
                        "deviation_score": round(max(z_v, z_a), 2),
                        "user_emotion": {
                            "valence": round(emotion.valence, 2),
                            "arousal": round(emotion.arousal, 2),
                        },
                        "group_emotion": {
                            "valence": round(group.valence, 2),
                            "arousal": round(group.arousal, 2),
                        },
                        "description": "情感孤岛" if z_v > 1.5 else "唤醒度异常",
                    }
                )
        return islands

    # ------------------------------------------------------------------
    # LLM fallback
    # ------------------------------------------------------------------

    def _build_persona_identity(self) -> str:
        """Build a concise persona description for the LLM cognition prompt."""
        if not self.persona and not self.ai_name:
            return ""

        parts: list[str] = []
        name = self.ai_name
        if self.persona:
            name = self.persona.name or name
        if name:
            parts.append(f"你是{name}。")

        if self.persona:
            p = self.persona
            if p.persona_summary:
                parts.append(p.persona_summary)
            elif p.backstory:
                # First sentence only, max 40 chars
                first = p.backstory.split("。")[0] + "。" if "。" in p.backstory else p.backstory
                parts.append(first[:60])
            if p.personality_traits:
                parts.append(f"你的性格是{'、'.join(p.personality_traits[:3])}。")
            if p.communication_style:
                parts.append(f"说话风格：{p.communication_style}。")
            if p.social_role:
                parts.append(f"在群里通常是{p.social_role}角色。")

        if not parts:
            return ""
        return "\n【角色身份】" + "".join(parts) + "\n"

    @staticmethod
    def _format_context_for_prompt(
        context_messages: list[dict[str, Any]] | None,
        max_turns: int = 4,
    ) -> str:
        """Format recent conversation context for LLM prompt."""
        if not context_messages:
            return ""
        lines: list[str] = ["\n最近对话上下文："]
        for msg in context_messages[-max_turns:]:
            uid = msg.get("user_id", "unknown")
            content = msg.get("content", "")
            if content:
                lines.append(f"[{uid}] {content}")
        return "\n".join(lines) + "\n"

    async def _llm_cognition(
        self,
        message: str,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Single LLM call for joint emotion + intent analysis."""
        from sirius_chat.providers.base import GenerationRequest, LLMProvider
        import asyncio

        persona_identity = self._build_persona_identity()
        if self.ai_name:
            ai_id = f"{persona_identity}当前 AI 名字：{self.ai_name}"
            if self.ai_aliases:
                ai_id += f"，别名：{', '.join(self.ai_aliases)}"
            ai_id += "\n"
            ai_note = (
                f"注意：如果消息中提到了当前 AI 的名字或别名，"
                f"social_intent 必须是 social（不是 silent），"
                f"且如果消息是提问或询问看法，urgency_score 至少为 80，relevance_score 至少为 0.75。\n"
            )
        else:
            ai_id = persona_identity
            ai_note = ""

        context_text = self._format_context_for_prompt(context_messages)
        prompt = _LLM_COGNITION_PROMPT.format(
            ai_identity=ai_id,
            message=context_text + message,
            ai_identity_note=ai_note,
        )
        request = GenerationRequest(
            model=self.model_name,
            system_prompt=prompt,
            messages=[],
            temperature=0.2,
            max_tokens=384,  # Slightly larger to fit both outputs
            purpose="cognition_analyze",
        )

        if hasattr(self.provider_async, "generate_async"):
            raw = await self.provider_async.generate_async(request)
        elif isinstance(self.provider_async, LLMProvider):
            raw = await asyncio.to_thread(self.provider_async.generate, request)
        else:
            return None

        try:
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]
            data = json.loads(raw.strip())

            # Parse emotion
            be_raw = data.get("basic_emotion", "neutral")
            basic_emotion = self._parse_basic_emotion(be_raw)

            emotion = EmotionState(
                valence=max(-1.0, min(1.0, float(data.get("valence", 0)))),
                arousal=max(0.0, min(1.0, float(data.get("arousal", 0.3)))),
                intensity=max(0.0, min(1.0, float(data.get("intensity", 0.5)))),
                confidence=0.85,
                basic_emotion=basic_emotion,
            )

            # Parse intent
            si_raw = data.get("social_intent", "social")
            social_intent = self._parse_social_intent(si_raw)

            subtype_str = data.get("intent_subtype", "topic_discussion")
            subtype = self._parse_subtype(subtype_str, social_intent)

            return {
                "emotion": emotion,
                "social_intent": social_intent,
                "subtype": subtype,
                "confidence": float(data.get("confidence", 0.85)),
                "urgency_score": float(data.get("urgency_score", 0)),
                "relevance_score": float(data.get("relevance_score", 0.5)),
                "search_query": data.get("search_query", ""),
            }
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Failed to parse LLM cognition JSON: %s | raw=%r", exc, raw)
            return None

    @staticmethod
    def _parse_basic_emotion(emotion_str: str) -> BasicEmotion | None:
        mapping = {
            "joy": BasicEmotion.JOY,
            "anger": BasicEmotion.ANGER,
            "sadness": BasicEmotion.SADNESS,
            "anxiety": BasicEmotion.ANXIETY,
            "loneliness": BasicEmotion.LONELINESS,
            "neutral": None,
        }
        return mapping.get(emotion_str.lower())

    @staticmethod
    def _parse_social_intent(intent_str: str) -> SocialIntent:
        mapping = {
            "help_seeking": SocialIntent.HELP_SEEKING,
            "emotional": SocialIntent.EMOTIONAL,
            "social": SocialIntent.SOCIAL,
            "silent": SocialIntent.SILENT,
        }
        return mapping.get(intent_str.lower(), SocialIntent.SOCIAL)

    @staticmethod
    def _parse_subtype(subtype_str: str, social_intent: SocialIntent) -> Any:
        """Parse subtype string into the correct Enum based on social_intent."""
        mapping: dict[str, Any] = {
            "tech_help": HelpSubtype.TECH_HELP,
            "info_query": HelpSubtype.INFO_QUERY,
            "venting": EmotionalSubtype.VENTING,
            "seeking_empathy": EmotionalSubtype.SEEKING_EMPATHY,
            "topic_discussion": SocialSubtype.TOPIC_DISCUSSION,
            "filler": SilentSubtype.FILLER,
        }
        subtype = mapping.get(subtype_str)
        if subtype is None:
            # Fallback based on social_intent
            if social_intent == SocialIntent.HELP_SEEKING:
                subtype = HelpSubtype.INFO_QUERY
            elif social_intent == SocialIntent.EMOTIONAL:
                subtype = EmotionalSubtype.SEEKING_EMPATHY
            elif social_intent == SocialIntent.SILENT:
                subtype = SilentSubtype.FILLER
            else:
                subtype = SocialSubtype.TOPIC_DISCUSSION
        return subtype

    # ------------------------------------------------------------------
    # Emotion text analysis (rule-based)
    # ------------------------------------------------------------------

    def _text_analysis(self, message: str) -> EmotionState:
        if not message:
            return EmotionState(valence=0.0, arousal=0.3, intensity=0.1, confidence=0.5)

        scores = []
        for word, score in self.lexicon.items():
            if word in message:
                scores.append(score)

        if not scores:
            return EmotionState(valence=0.0, arousal=0.3, intensity=0.1, confidence=0.5)

        avg = sum(scores) / len(scores)
        intensity = min(
            1.0, len(scores) / max(1, len(message)) * 3 + self._punctuation_intensity(message)
        )
        arousal = self._estimate_arousal(message, scores)
        return EmotionState(
            valence=max(-1.0, min(1.0, avg)),
            arousal=arousal,
            intensity=intensity,
            confidence=0.7 if len(scores) >= 2 else 0.5,
        )

    @staticmethod
    def _punctuation_intensity(message: str) -> float:
        intensity = 0.0
        intensity += min(0.3, (message.count("!") + message.count("！")) * 0.1)
        if message.count("?") + message.count("？") >= 3:
            intensity += 0.2
        if "..." in message or "…" in message:
            intensity += 0.1
        return min(0.5, intensity)

    @staticmethod
    def _estimate_arousal(message: str, sentiment_scores: list[float]) -> float:
        avg_abs = sum(abs(s) for s in sentiment_scores) / len(sentiment_scores)
        upper_ratio = sum(1 for c in message if c.isupper()) / max(1, len(message))
        length_factor = 1.0 - min(1.0, len(message) / 200.0)
        arousal = avg_abs * 0.5 + upper_ratio * 0.3 + length_factor * 0.2
        return max(0.0, min(1.0, arousal))

    # ------------------------------------------------------------------
    # Emotion context inference
    # ------------------------------------------------------------------

    def _context_inference(self, user_id: str) -> EmotionState | None:
        traj = self.trajectories.get(user_id, [])
        if len(traj) < 2:
            return None
        recent = [state for _, state in traj[-5:]]
        valence_trend = recent[-1].valence - recent[0].valence
        arousal_trend = recent[-1].arousal - recent[0].arousal
        return EmotionState(
            valence=max(-1.0, min(1.0, recent[-1].valence + valence_trend * 0.3)),
            arousal=max(0.0, min(1.0, recent[-1].arousal + arousal_trend * 0.3)),
            intensity=recent[-1].intensity,
            confidence=0.6,
        )

    def _update_trajectory(self, user_id: str, emotion: EmotionState) -> None:
        if user_id not in self.trajectories:
            self.trajectories[user_id] = []
        from sirius_chat.core.utils import now_iso
        self.trajectories[user_id].append((now_iso(), emotion))
        if len(self.trajectories[user_id]) > 100:
            self.trajectories[user_id] = self.trajectories[user_id][-100:]

    @staticmethod
    def _fuse_emotion(
        text: EmotionState,
        context: EmotionState | None,
        group: EmotionState | None,
    ) -> EmotionState:
        w_text = 0.5
        w_context = 0.3 if context else 0.0
        w_group = 0.2 if group else 0.0
        total = w_text + w_context + w_group
        w_text /= total
        w_context = (w_context / total) if w_context else 0.0
        w_group = (w_group / total) if w_group else 0.0

        valence = text.valence * w_text
        arousal = text.arousal * w_text
        if context:
            valence += context.valence * w_context
            arousal += context.arousal * w_context
        if group:
            valence += group.valence * w_group
            arousal += group.arousal * w_group

        return EmotionState(
            valence=max(-1.0, min(1.0, valence)),
            arousal=max(0.0, min(1.0, arousal)),
            intensity=text.intensity,
            confidence=text.confidence,
        )

    # ------------------------------------------------------------------
    # Intent classification (rule-based)
    # ------------------------------------------------------------------

    def _detect_directed_at_ai(self, message: str) -> bool:
        """Check if message directly addresses the current AI by name or alias."""
        if not self.ai_name:
            return False
        text = message.lower()
        names = [self.ai_name.lower()] + self.ai_aliases
        return any(name in text for name in names if name)

    def _classify_intent(
        self,
        message: str,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> tuple[SocialIntent, Any, float]:
        text = message.lower()
        has_context = bool(context_messages)

        # Help seeking
        help_score = 0
        for pat in _HELP_PATTERNS:
            if re.search(pat, text):
                help_score += 1
        if "?" in message or "？" in message:
            help_score += 1

        # Emotional
        emotional_score = sum(1 for w in _EMOTIONAL_INDICATORS if w in text)

        # Social
        social_score = sum(1 for w in _SOCIAL_INDICATORS if w in text)

        # Context-aware: short messages that look like filler may actually be
        # follow-ups to previous messages (e.g. "为什么？", "那怎么办", "懂了")
        if has_context and len(message) <= 8:
            if any(p in message for p in _CONTEXT_DEPENDENT_PATTERNS):
                # This is likely a conversational follow-up, not filler
                if help_score >= 1:
                    return SocialIntent.HELP_SEEKING, HelpSubtype.INFO_QUERY, 0.75
                return SocialIntent.SOCIAL, SocialSubtype.TOPIC_DISCUSSION, 0.65

        # Silent indicators (filler)
        if len(message) <= 4 or message in {"哈哈", "确实", "+1", "嗯", "哦"}:
            return SocialIntent.SILENT, SilentSubtype.FILLER, 0.9

        if help_score >= 1 and help_score >= emotional_score and help_score >= social_score:
            subtype = (
                HelpSubtype.TECH_HELP
                if any(k in text for k in {"报错", "错误", "exception", "bug"})
                else HelpSubtype.INFO_QUERY
            )
            return SocialIntent.HELP_SEEKING, subtype, min(0.95, 0.6 + help_score * 0.1)

        if emotional_score >= 2 and emotional_score >= help_score and emotional_score >= social_score:
            subtype = (
                EmotionalSubtype.VENTING
                if any(k in text for k in {"烦", "累", "难受", "崩溃"})
                else EmotionalSubtype.SEEKING_EMPATHY
            )
            return SocialIntent.EMOTIONAL, subtype, min(0.9, 0.5 + emotional_score * 0.1)

        if social_score >= 1:
            subtype = SocialSubtype.TOPIC_DISCUSSION
            return SocialIntent.SOCIAL, subtype, min(0.8, 0.5 + social_score * 0.1)

        # Default: social or silent based on length
        if len(message) <= 10:
            return SocialIntent.SILENT, SilentSubtype.FILLER, 0.6
        return SocialIntent.SOCIAL, SocialSubtype.TOPIC_DISCUSSION, 0.5

    # ------------------------------------------------------------------
    # Intent scoring
    # ------------------------------------------------------------------

    def _calculate_urgency(
        self,
        message: str,
        user_id: str,
        group_id: str | None,
        emotion: EmotionState | None,
        context_messages: list[dict[str, Any]] | None = None,
    ) -> float:
        text = message.lower()
        score = 0.0

        # Language markers (0-25)
        if any(kw in text for kw in _URGENCY_KEYWORDS["high"]):
            score += 25.0
        elif any(kw in text for kw in _URGENCY_KEYWORDS["medium"]):
            score += 12.0
        elif any(kw in text for kw in _URGENCY_KEYWORDS["low"]):
            score += 5.0

        # Time constraint (0-15)
        if any(kw in text for kw in {"明天", "今天", "马上", "立刻", "今晚", " asap"}):
            score += 15.0

        # Emotional intensity (0-18)
        if emotion:
            if emotion.valence < -0.5 and emotion.arousal > 0.7:
                score += 18.0
            elif emotion.intensity > 0.7:
                score += 12.0

        # Context-aware: follow-up questions in short messages carry implicit urgency
        if context_messages and len(message) <= 8:
            if any(p in message for p in _CONTEXT_DEPENDENT_PATTERNS):
                score += 20.0

        return max(0.0, min(100.0, score))

    def _calculate_relevance(
        self,
        message: str,
        social_intent: SocialIntent,
        user_id: str,
        group_id: str | None,
    ) -> float:
        # Lower base relevance to reduce overall reply frequency.
        # Only help-seeking and emotional intents get a modest boost.
        role_match = 0.8 if social_intent in (SocialIntent.HELP_SEEKING, SocialIntent.EMOTIONAL) else 0.1
        return min(1.0, 0.22 + role_match * 0.4)

    def _dynamic_threshold(self, group_id: str, user_id: str) -> float:
        base = 0.60 - 0.5 * 0.30  # 0.45
        activity = 1.0
        relationship = 1.0
        time_f = 1.0
        return base * activity * relationship * time_f

    def _decide_strategy(
        self,
        social_intent: SocialIntent,
        urgency: float,
        relevance: float,
        threshold: float,
    ) -> tuple[str, int, float]:
        if urgency >= 80 and relevance >= 0.7:
            return "immediate", 1, 0.0
        if urgency >= 50 and relevance >= 0.55:
            return "delayed", 2, 15.0
        if urgency >= 25 and relevance >= 0.5:
            return "delayed", 4, 45.0
        return "silent", 8, 0.0

    @staticmethod
    def _intent_type_from_social(social_intent: SocialIntent, message: str) -> str:
        if social_intent == SocialIntent.HELP_SEEKING:
            return "question" if "?" in message or "？" in message else "request"
        if social_intent in (SocialIntent.EMOTIONAL, SocialIntent.SOCIAL):
            return "chat"
        return "chat"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


