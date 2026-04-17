"""Intent analyzer v3: purpose-driven classification (paper §2).

Extends v2 behavior classification with social-intent taxonomy:
    HELP_SEEKING | EMOTIONAL | SOCIAL | SILENT

Urgency (0-100) and relevance (0-1) scoring per paper §2.2.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sirius_chat.models.emotion import EmotionState
from sirius_chat.models.intent_v3 import (
    EmotionalSubtype,
    HelpSubtype,
    IntentAnalysisV3,
    SilentSubtype,
    SocialIntent,
    SocialSubtype,
)

logger = logging.getLogger(__name__)

_HELP_PATTERNS = [
    r"怎么\s*\S+", r"如何\s*\S+", r"为什么\s*\S+",
    r"有人.*吗", r"求助", r"请教", r"大佬", r"救命",
    r"报错", r"错误", r"exception", r"error", r"failed",
]

_EMOTIONAL_INDICATORS = [
    "感觉", "觉得", "心情", "难受", "开心", "难过",
    "累", "烦", "郁闷", "兴奋", "sad", "happy",
    "upset", "excited", "tired", "孤独", "寂寞", "压力",
]

_SOCIAL_INDICATORS = [
    "大家觉得", "有没有人", "一起", "推荐", "分享",
    "讨论", "聊聊", "怎么样", "如何看",
]

_URGENCY_KEYWORDS = {
    "high": {"崩溃", "救命", "急", "马上", "立刻", "现在", "死了", "完了", "urgent", "emergency", "asap", "help", "broken", "crash"},
    "medium": {"求助", "请问", "怎么", "如何", "为什么", "不懂", "不会", "confused", "stuck", "problem", "issue", "question"},
    "low": {"想问问", "好奇", "了解一下", "有空的话", "方便时", "wondering", "curious", "when you have time"},
}


class IntentAnalyzerV3:
    """Purpose-driven intent analyzer with urgency/relevance scoring."""

    def __init__(self) -> None:
        self.group_activity_history: dict[str, list[tuple[float, float]]] = {}
        self.user_response_prefs: dict[str, dict[str, Any]] = {}

    def analyze(
        self,
        message: str,
        user_id: str,
        group_id: str,
        *,
        emotion_state: EmotionState | None = None,
    ) -> IntentAnalysisV3:
        """Main analysis pipeline."""
        # 1. Classify social intent
        social_intent, subtype, confidence = self._classify_intent(message)

        # 2. Calculate urgency
        urgency = self._calculate_urgency(message, user_id, group_id, emotion_state)

        # 3. Calculate relevance
        relevance = self._calculate_relevance(message, social_intent, user_id, group_id)

        # 4. Dynamic threshold
        threshold = self._dynamic_threshold(group_id, user_id)

        # 5. Decide strategy
        strategy, priority, response_time = self._decide_strategy(
            social_intent, urgency, relevance, threshold
        )

        return IntentAnalysisV3(
            intent_type=self._intent_type_from_social(social_intent, message),
            social_intent=social_intent,
            intent_subtype=subtype.value,
            urgency_score=urgency,
            relevance_score=relevance,
            confidence=confidence,
            response_priority=priority,
            estimated_response_time=response_time,
            threshold=threshold,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify_intent(self, message: str) -> tuple[SocialIntent, Any, float]:
        text = message.lower()

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

        # Silent indicators (filler)
        if len(message) <= 4 or message in {"哈哈", "确实", "+1", "嗯", "哦"}:
            return SocialIntent.SILENT, SilentSubtype.FILLER, 0.9

        if help_score >= 1 and help_score >= emotional_score and help_score >= social_score:
            subtype = HelpSubtype.TECH_HELP if any(k in text for k in {"报错", "错误", "exception", "bug"}) else HelpSubtype.INFO_QUERY
            return SocialIntent.HELP_SEEKING, subtype, min(0.95, 0.6 + help_score * 0.1)

        if emotional_score >= 2 and emotional_score >= help_score and emotional_score >= social_score:
            subtype = EmotionalSubtype.VENTING if any(k in text for k in {"烦", "累", "难受", "崩溃"}) else EmotionalSubtype.SEEKING_EMPATHY
            return SocialIntent.EMOTIONAL, subtype, min(0.9, 0.5 + emotional_score * 0.1)

        if social_score >= 1:
            subtype = SocialSubtype.TOPIC_DISCUSSION
            return SocialIntent.SOCIAL, subtype, min(0.8, 0.5 + social_score * 0.1)

        # Default: social or silent based on length
        if len(message) <= 10:
            return SocialIntent.SILENT, SilentSubtype.FILLER, 0.6
        return SocialIntent.SOCIAL, SocialSubtype.TOPIC_DISCUSSION, 0.5

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _calculate_urgency(
        self,
        message: str,
        user_id: str,
        group_id: str,
        emotion: EmotionState | None,
    ) -> float:
        text = message.lower()
        score = 0.0

        # Language markers (0.25)
        if any(kw in text for kw in _URGENCY_KEYWORDS["high"]):
            score += 25.0
        elif any(kw in text for kw in _URGENCY_KEYWORDS["medium"]):
            score += 12.0
        elif any(kw in text for kw in _URGENCY_KEYWORDS["low"]):
            score += 5.0

        # Time constraint (0.20)
        if any(kw in text for kw in {"明天", "今天", "马上", "立刻", "今晚", " asap"}):
            score += 15.0

        # Emotional intensity (0.20)
        if emotion:
            if emotion.valence < -0.5 and emotion.arousal > 0.7:
                score += 18.0
            elif emotion.intensity > 0.7:
                score += 12.0

        # Repeated signal (0.20) - placeholder
        # Group response (0.15) - placeholder

        return max(0.0, min(100.0, score))

    def _calculate_relevance(
        self,
        message: str,
        social_intent: SocialIntent,
        user_id: str,
        group_id: str,
    ) -> float:
        # Topic match (0.4) - placeholder using keyword overlap
        # Role match (0.3)
        role_match = 0.7 if social_intent in (SocialIntent.HELP_SEEKING, SocialIntent.EMOTIONAL) else 0.4
        # History match (0.3) - placeholder
        return min(1.0, 0.5 + role_match * 0.3)

    def _dynamic_threshold(self, group_id: str, user_id: str) -> float:
        # Base threshold (sensitivity default 0.5)
        base = 0.60 - 0.5 * 0.30  # 0.45
        # Activity factor (placeholder)
        activity = 1.0
        # Relationship factor (placeholder)
        relationship = 1.0
        # Time factor (placeholder)
        time_f = 1.0
        return base * activity * relationship * time_f

    def _decide_strategy(
        self,
        social_intent: SocialIntent,
        urgency: float,
        relevance: float,
        threshold: float,
    ) -> tuple[str, int, float]:
        """Decide response strategy and timing."""
        if urgency >= 80 and relevance >= 0.7:
            return "immediate", 1, 0.0
        if urgency >= 50 and relevance >= 0.5:
            return "delayed", 2, 15.0
        if urgency >= 20 and relevance >= 0.5:
            return "delayed", 4, 45.0
        return "silent", 8, 0.0

    @staticmethod
    def _intent_type_from_social(social_intent: SocialIntent, message: str) -> str:
        if social_intent == SocialIntent.HELP_SEEKING:
            return "question" if "?" in message or "？" in message else "request"
        if social_intent == SocialIntent.EMOTIONAL:
            return "chat"
        if social_intent == SocialIntent.SOCIAL:
            return "chat"
        return "reaction"
