"""Basic sanity tests for the new EmotionalGroupChatEngine (v0.28+)."""

from __future__ import annotations

import pytest

from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.models.models import Message, Participant
from sirius_chat.models.persona import PersonaProfile


@pytest.fixture
def engine(tmp_path):
    return EmotionalGroupChatEngine(
        work_path=tmp_path,
        persona=PersonaProfile(name="TestBot"),
    )


@pytest.mark.asyncio
async def test_engine_process_message_returns_result(engine):
    msg = Message(role="human", content="大家好", speaker="user_1", group_id="group_a")
    participant = Participant(name="user_1", user_id="user_1")
    result = await engine.process_message(msg, [participant], "group_a")
    assert "strategy" in result
    assert "emotion" in result
    assert "intent" in result


@pytest.mark.asyncio
async def test_engine_silent_strategy_for_filler(engine):
    msg = Message(role="human", content="哈哈", speaker="user_1", group_id="group_a")
    participant = Participant(name="user_1", user_id="user_1")
    result = await engine.process_message(msg, [participant], "group_a")
    assert result["strategy"] in ("silent", "delayed", "immediate")


@pytest.mark.asyncio
async def test_engine_help_seeking_urgency(engine):
    msg = Message(role="human", content="崩溃了！救命！", speaker="user_1", group_id="group_a")
    participant = Participant(name="user_1", user_id="user_1")
    result = await engine.process_message(msg, [participant], "group_a")
    intent = result["intent"]
    assert intent["urgency_score"] >= 20


@pytest.mark.asyncio
async def test_working_memory_isolation(engine):
    msg1 = Message(role="human", content="hello", speaker="u1", group_id="group_a")
    msg2 = Message(role="human", content="world", speaker="u1", group_id="group_b")
    p = Participant(name="u1", user_id="u1")
    await engine.process_message(msg1, [p], "group_a")
    await engine.process_message(msg2, [p], "group_b")
    wa = engine.working_memory.get_window("group_a")
    wb = engine.working_memory.get_window("group_b")
    assert len(wa) == 1
    assert len(wb) == 1
    assert wa[0].content == "hello"
    assert wb[0].content == "world"


@pytest.mark.asyncio
async def test_proactive_trigger_silence(engine):
    # No messages -> should potentially trigger after silence threshold
    result = engine.proactive_trigger.check(
        "group_a",
        last_message_at=None,
    )
    # Without last_message_at, silence trigger can't fire
    assert result is None


@pytest.mark.asyncio
async def test_cognition_analyzer_basic(engine):
    emotion, intent, empathy = await engine.cognition_analyzer.analyze(
        "太开心了！", "user_1", "group_a"
    )
    assert emotion.valence > 0.3
    assert emotion.basic_emotion is not None
    assert intent.social_intent is not None
    assert empathy.strategy_type is not None


def test_rhythm_analyzer_basic(engine):
    messages = [
        {"user_id": "u1", "content": "hi", "timestamp": "2026-04-17T10:00:00+00:00"},
        {"user_id": "u2", "content": "hello", "timestamp": "2026-04-17T10:00:05+00:00"},
    ]
    rhythm = engine.rhythm_analyzer.analyze("group_a", messages)
    assert rhythm.heat_level in ("cold", "warm", "hot", "overheated")
    assert rhythm.pace in ("accelerating", "steady", "decelerating", "silent")


def test_strategy_engine_decision(engine):
    from sirius_chat.models.intent_v3 import IntentAnalysisV3, SocialIntent
    intent = IntentAnalysisV3(
        social_intent=SocialIntent.HELP_SEEKING,
        urgency_score=90,
        relevance_score=0.9,
    )
    decision = engine.strategy_engine.decide(intent, is_mentioned=True)
    assert decision.strategy.value == "immediate"


def test_delayed_queue_enqueue_and_tick(engine):
    from sirius_chat.models.response_strategy import StrategyDecision
    decision = StrategyDecision(
        strategy=__import__("sirius_chat.models.response_strategy", fromlist=["ResponseStrategy"]).ResponseStrategy.DELAYED,
        urgency=50,
        relevance=0.6,
    )
    item = engine.delayed_queue.enqueue(
        group_id="group_a",
        user_id="u1",
        message_content="test",
        strategy_decision=decision,
    )
    assert item.status == "pending"
    triggered = engine.delayed_queue.tick("group_a", [])
    # Should not trigger immediately (window not expired, no gap)
    assert len(triggered) == 0
    pending = engine.delayed_queue.get_pending("group_a")
    assert len(pending) == 1
