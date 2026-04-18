"""Tests for CognitionAnalyzer: unified emotion + intent analysis."""

from __future__ import annotations

import pytest

from sirius_chat.core.cognition import CognitionAnalyzer
from sirius_chat.models.emotion import EmotionState
from sirius_chat.models.intent_v3 import SocialIntent, IntentAnalysisV3


class TestCognitionAnalyzerRules:
    @pytest.mark.asyncio
    async def test_positive_emotion_detected(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("太开心了！", "u1", "g1")
        assert emotion.valence > 0.3
        assert emotion.basic_emotion is not None

    @pytest.mark.asyncio
    async def test_negative_emotion_detected(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("好难过，崩溃了", "u1", "g1")
        assert emotion.valence < -0.3

    @pytest.mark.asyncio
    async def test_help_seeking_intent(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("怎么安装Python？", "u1", "g1")
        assert intent.social_intent == SocialIntent.HELP_SEEKING

    @pytest.mark.asyncio
    async def test_emotional_intent(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("感觉好孤独", "u1", "g1")
        assert intent.social_intent == SocialIntent.EMOTIONAL

    @pytest.mark.asyncio
    async def test_silent_intent_short_message(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("哈哈", "u1", "g1")
        assert intent.social_intent == SocialIntent.SILENT

    @pytest.mark.asyncio
    async def test_empathy_strategy_for_negative(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("愤怒！完全无法接受！", "u1", "g1")
        # Negative valence -> cognitive or confirm_action (both negative strategies)
        assert empathy.strategy_type in ("cognitive", "confirm_action")
        assert empathy.priority <= 2

    @pytest.mark.asyncio
    async def test_empathy_strategy_for_positive(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("太棒了！超级开心！", "u1", "g1")
        assert empathy.strategy_type == "share_joy"


class TestCognitionAnalyzerContext:
    @pytest.mark.asyncio
    async def test_trajectory_tracking(self):
        ca = CognitionAnalyzer()
        await ca.analyze("还行", "u1", "g1")
        await ca.analyze("不错", "u1", "g1")
        assert "u1" in ca.trajectories
        assert len(ca.trajectories["u1"]) == 2

    @pytest.mark.asyncio
    async def test_group_sentiment_update(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("好开心", "u1", "g1")
        ca.update_group_sentiment("g1", emotion)
        assert "g1" in ca.group_cache
        assert ca.group_cache["g1"].valence > 0

    @pytest.mark.asyncio
    async def test_emotion_islands_detect_outlier(self):
        ca = CognitionAnalyzer()
        ca.update_group_sentiment("g1", EmotionState(valence=0.2, arousal=0.3))
        recent = {
            "alice": EmotionState(valence=0.2, arousal=0.3),
            "bob": EmotionState(valence=0.15, arousal=0.25),
            "charlie": EmotionState(valence=-0.9, arousal=0.8),
        }
        islands = ca.detect_emotion_islands("g1", recent)
        assert len(islands) == 1
        assert islands[0]["user_id"] == "charlie"

    @pytest.mark.asyncio
    async def test_emotion_islands_no_outliers(self):
        ca = CognitionAnalyzer()
        recent = {
            "alice": EmotionState(valence=0.1, arousal=0.2),
            "bob": EmotionState(valence=0.15, arousal=0.25),
        }
        islands = ca.detect_emotion_islands("g1", recent)
        assert islands == []


class TestCognitionAnalyzerUrgency:
    @pytest.mark.asyncio
    async def test_high_urgency_keywords(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("崩溃了，救命啊！", "u1", "g1")
        assert intent.urgency_score >= 25

    @pytest.mark.asyncio
    async def test_emotion_boosts_urgency(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("绝望了，怎么办", "u1", "g1")
        # Negative valence + high arousal should boost urgency
        assert intent.urgency_score > 0


class TestCognitionAnalyzerFusion:
    @pytest.mark.asyncio
    async def test_empty_message(self):
        ca = CognitionAnalyzer()
        emotion, intent, empathy = await ca.analyze("", "u1", "g1")
        assert emotion.confidence == 0.5
        assert intent.social_intent == SocialIntent.SILENT
