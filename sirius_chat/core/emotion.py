"""Emotion analyzer: 2D valence-arousal model with empathy strategy selection.

Hybrid architecture: rule-based engine (zero LLM cost) + optional LLM fallback
for ambiguous or complex emotional content. Aligned with paper §3.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from sirius_chat.models.emotion import BasicEmotion, EmotionState, EmpathyStrategy

logger = logging.getLogger(__name__)

_LLM_EMOTION_PROMPT = """分析以下消息的情感状态，输出结构化 JSON。

消息：{message}

要求输出 JSON：
{{
  "valence": -1.0 到 1.0（愉悦度，负值负面，正值正面）,
  "arousal": 0.0 到 1.0（唤醒度，0平静，1激动）,
  "intensity": 0.0 到 1.0（情感强度）,
  "basic_emotion": "joy|anger|sadness|anxiety|loneliness|neutral"
}}

只输出 JSON，不要其他内容。"""

# Built-in sentiment lexicon (Chinese + internet slang)
_DEFAULT_LEXICON: dict[str, float] = {
    # Positive
    "开心": 0.8, "高兴": 0.9, "快乐": 0.85, "棒": 0.8, "好": 0.6,
    "喜欢": 0.7, "爱": 0.9, "感动": 0.7, "欣慰": 0.6, "满足": 0.7,
    "期待": 0.5, "兴奋": 0.85, "激动": 0.8, "惊喜": 0.7, "感谢": 0.6,
    "哈哈": 0.5, "嘿嘿": 0.4, "yyds": 0.9, "xswl": 0.8, "awsl": 0.7,
    "绝绝子": 0.7, "赞": 0.7, "牛逼": 0.7, "太棒了": 0.8,
    # Negative
    "难过": -0.7, "伤心": -0.8, "悲伤": -0.85, "痛苦": -0.9,
    "生气": -0.6, "愤怒": -0.8, "恼火": -0.5, "烦": -0.5,
    "讨厌": -0.6, "恶心": -0.7, "厌恶": -0.6, "失望": -0.6,
    "害怕": -0.7, "担心": -0.5, "焦虑": -0.6, "紧张": -0.5,
    "累": -0.4, "疲惫": -0.5, "绝望": -0.9, "崩溃": -0.9,
    "无语": -0.3, "郁闷": -0.5, "emo": -0.6, "蚌埠住了": -0.3,
    "呜呜": -0.6, "泪目": -0.4, "扎心": -0.5, "难受": -0.6,
    # Ambiguous / context-dependent (lower weight)
    "确实": 0.0, "好吧": -0.1, "哦": 0.0, "嗯": 0.0,
}


class EmotionAnalyzer:
    """Analyzes emotional content of messages using 2D valence-arousal model.

    Hybrid: rule engine (fast, zero cost) + optional LLM fallback for
    complex or ambiguous emotional content.
    """

    def __init__(
        self,
        lexicon: dict[str, float] | None = None,
        provider_async: Any | None = None,
        model_name: str = "gpt-4o-mini",
    ) -> None:
        self.lexicon = lexicon or dict(_DEFAULT_LEXICON)
        self.provider_async = provider_async
        self.model_name = model_name
        # User emotion trajectories: user_id -> list of (timestamp, EmotionState)
        self.trajectories: dict[str, list[tuple[str, EmotionState]]] = {}
        # Group emotion cache: group_id -> EmotionState
        self.group_cache: dict[str, EmotionState] = {}
        # User empathy style preferences
        self.empathy_prefs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        message: str,
        user_id: str,
        group_id: str | None = None,
    ) -> EmotionState:
        """Analyze emotion of a message.

        1. Text sentiment analysis (rule-based)
        2. LLM fallback for ambiguous cases (confidence < 0.6)
        3. Context inference (trajectory trend)
        4. Group sentiment perception (cache)
        5. Fuse with weighted average
        """
        text_emotion = self._text_analysis(message)

        # LLM fallback for low-confidence rule results
        if text_emotion.confidence < 0.6 and self.provider_async is not None:
            try:
                llm_result = await self._llm_analyze(message)
                if llm_result is not None:
                    text_emotion = llm_result
            except Exception as exc:
                logger.warning("LLM emotion analysis failed: %s", exc)

        context_emotion = self._context_inference(user_id)
        group_emotion = self.group_cache.get(group_id) if group_id else None

        final = self._fuse(text_emotion, context_emotion, group_emotion)
        self._update_trajectory(user_id, final)
        return final

    async def _llm_analyze(self, message: str) -> EmotionState | None:
        """Call LLM for high-precision emotion analysis."""
        from sirius_chat.providers.base import GenerationRequest, LLMProvider
        import asyncio

        prompt = _LLM_EMOTION_PROMPT.format(message=message)
        request = GenerationRequest(
            model=self.model_name,
            system_prompt=prompt,
            messages=[],
            temperature=0.2,
            max_tokens=256,
            purpose="emotion_analyze",
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
            return EmotionState(
                valence=max(-1.0, min(1.0, float(data.get("valence", 0)))),
                arousal=max(0.0, min(1.0, float(data.get("arousal", 0.3)))),
                intensity=max(0.0, min(1.0, float(data.get("intensity", 0.5)))),
                confidence=0.85,
                basic_emotion=self._parse_basic_emotion(data.get("basic_emotion", "neutral")),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Failed to parse LLM emotion JSON: %s | raw=%r", exc, raw)
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

    def update_group_emotion(
        self,
        group_id: str,
        messages: list[tuple[str, str, str]],  # (user_id, content, timestamp)
    ) -> EmotionState | None:
        """Update group emotion cache from recent messages."""
        if not messages:
            return None
        emotions = []
        for user_id, content, _ in messages[-20:]:
            emotions.append(self._text_analysis(content))
        if not emotions:
            return None

        weights = [math.exp(-0.05 * i) for i in range(len(emotions))]
        total = sum(weights)
        weights = [w / total for w in weights]

        avg_valence = sum(e.valence * w for e, w in zip(emotions, weights))
        avg_arousal = sum(e.arousal * w for e, w in zip(emotions, weights))
        avg_intensity = sum(e.intensity for e in emotions) / len(emotions)

        state = EmotionState(
            valence=max(-1.0, min(1.0, avg_valence)),
            arousal=max(0.0, min(1.0, avg_arousal)),
            intensity=avg_intensity,
            confidence=0.7,
        )
        self.group_cache[group_id] = state
        return state

    def select_empathy_strategy(
        self,
        emotion: EmotionState,
        user_id: str,
    ) -> EmpathyStrategy:
        """Select empathy strategy based on emotion state (paper §3.2)."""
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
    # Text analysis (rule-based)
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
        intensity = min(1.0, len(scores) / max(1, len(message)) * 3 + self._punctuation_intensity(message))
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
    # Context inference
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
        self.trajectories[user_id].append((self._now_iso(), emotion))
        if len(self.trajectories[user_id]) > 100:
            self.trajectories[user_id] = self.trajectories[user_id][-100:]

    # ------------------------------------------------------------------
    # Group sentiment
    # ------------------------------------------------------------------

    def update_group_sentiment(self, group_id: str, emotion: EmotionState) -> None:
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
            alpha = 0.3  # EMA smoothing factor
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
        """Detect users whose emotion deviates significantly from group mean.

        Returns list of dicts with user_id, deviation_score, and description.
        """
        if not recent_emotions or len(recent_emotions) < 2:
            return []

        group = self.group_cache.get(group_id)
        if group is None:
            # Compute mean from recent emotions
            valences = [e.valence for e in recent_emotions.values()]
            arousals = [e.arousal for e in recent_emotions.values()]
            group = EmotionState(
                valence=sum(valences) / len(valences),
                arousal=sum(arousals) / len(arousals),
                intensity=0.5,
                confidence=0.5,
            )

        # Compute standard deviation
        valences = [e.valence for e in recent_emotions.values()]
        mean_v = sum(valences) / len(valences)
        std_v = math.sqrt(sum((v - mean_v) ** 2 for v in valences) / len(valences)) if len(valences) > 1 else 0.0

        arousals = [e.arousal for e in recent_emotions.values()]
        mean_a = sum(arousals) / len(arousals)
        std_a = math.sqrt(sum((a - mean_a) ** 2 for a in arousals) / len(arousals)) if len(arousals) > 1 else 0.0

        islands = []
        for user_id, emotion in recent_emotions.items():
            dev_v = abs(emotion.valence - group.valence)
            dev_a = abs(emotion.arousal - group.arousal)
            # Normalize by std if available, else use raw threshold
            z_v = dev_v / std_v if std_v > 0.01 else dev_v * 2
            z_a = dev_a / std_a if std_a > 0.01 else dev_a * 2
            if z_v > 1.5 or z_a > 1.5:
                islands.append({
                    "user_id": user_id,
                    "deviation_score": round(max(z_v, z_a), 2),
                    "user_emotion": {"valence": round(emotion.valence, 2), "arousal": round(emotion.arousal, 2)},
                    "group_emotion": {"valence": round(group.valence, 2), "arousal": round(group.arousal, 2)},
                    "description": "情感孤岛" if z_v > 1.5 else "唤醒度异常",
                })
        return islands

    # ------------------------------------------------------------------
    # Fusion
    # ------------------------------------------------------------------

    @staticmethod
    def _fuse(
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

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
