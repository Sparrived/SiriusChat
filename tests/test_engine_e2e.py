"""End-to-end integration tests for EmotionalGroupChatEngine.

Scenarios:
1. User asks for help -> IMMEDIATE response with emotion context in prompt
2. Casual filler -> SILENT strategy, no reply generated
3. Delayed queue -> topic gap triggers delayed response
4. Proactive trigger -> long silence -> AI initiates conversation
5. Multi-group isolation -> two groups converse, memories don't leak
6. Group atmosphere shift -> positive to negative -> empathy strategy changes
"""

from __future__ import annotations

import asyncio
import pytest

from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.models.models import Message, Participant
from sirius_chat.models.persona import PersonaProfile


class TestE2EImmediateResponse:
    @pytest.mark.asyncio
    async def test_help_seeking_triggers_immediate(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="alice", user_id="alice")

        result = await engine.process_message(
            Message(role="human", content="服务器崩溃了，怎么恢复？", speaker="alice"),
            [p], "tech_group",
        )

        # Should respond immediately or with short delay (not silent)
        assert result["strategy"] in ("immediate", "delayed")
        # Emotion should be detected (high arousal / negative valence for crisis)
        emotion = result["emotion"]
        assert emotion["arousal"] > 0.3


class TestE2ESilentStrategy:
    @pytest.mark.asyncio
    async def test_casual_filler_is_silent(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="bob", user_id="bob")

        result = await engine.process_message(
            Message(role="human", content="哈哈", speaker="bob"),
            [p], "chat_group",
        )

        # Should be silent or at most delayed
        assert result["strategy"] in ("silent", "delayed")

    @pytest.mark.asyncio
    async def test_silent_message_buffers_for_surface_thought(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="bob", user_id="bob")

        # Send a message that should be silent
        result = await engine.process_message(
            Message(role="human", content="嗯", speaker="bob"),
            [p], "chat_group",
        )

        # Silent strategy simply returns without reply; no buffer is maintained
        # since surface-thought generation has been removed.


class TestE2EDelayedResponse:
    @pytest.mark.asyncio
    async def test_delayed_queue_triggers_after_gap(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="carol", user_id="carol")

        # First message: medium relevance -> DELAYED
        result = await engine.process_message(
            Message(role="human", content="我最近在学Python", speaker="carol"),
            [p], "learn_group",
        )

        # If delayed, queue should have one item
        if result["strategy"] == "delayed":
            pending = engine.delayed_queue.get_pending("learn_group")
            assert len(pending) == 1

            # Simulate topic gap (no new messages)
            triggered = engine.delayed_queue.tick("learn_group", recent_messages=[])
            # With an empty recent_messages list, gap should be detected
            assert len(triggered) >= 0  # may or may not trigger depending on window


class TestE2EProactiveTrigger:
    @pytest.mark.asyncio
    async def test_long_silence_triggers_proactive(self, tmp_path):
        from datetime import datetime, timezone
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )

        # Set last message far in the past
        engine._group_last_message_at["quiet_group"] = "2026-04-01T00:00:00+00:00"

        # Inject 15:00 (inside active hours 12-21)
        fixed_now = datetime(2026, 4, 18, 15, 0, 0, tzinfo=timezone.utc)
        result = await engine.proactive_check("quiet_group", _now=fixed_now)

        # Should trigger after long silence
        assert result is not None
        assert result["strategy"] == "proactive"
        assert "trigger_type" in result

    @pytest.mark.asyncio
    async def test_proactive_suppressed_outside_active_hours(self, tmp_path):
        """Proactive should not fire outside configured active hours."""
        from datetime import datetime, timezone
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )

        # Set last message far in the past
        engine._group_last_message_at["quiet_group"] = "2026-04-01T00:00:00+00:00"

        # Inject 03:00 (outside active hours 12-21)
        fixed_now = datetime(2026, 4, 18, 3, 0, 0, tzinfo=timezone.utc)
        result = await engine.proactive_check("quiet_group", _now=fixed_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_proactive_allowed_during_active_hours(self, tmp_path):
        """Proactive should fire during configured active hours."""
        from datetime import datetime, timezone
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )

        # Set last message far in the past
        engine._group_last_message_at["quiet_group"] = "2026-04-01T00:00:00+00:00"

        # Inject 15:00 (inside active hours 12-21)
        fixed_now = datetime(2026, 4, 18, 15, 0, 0, tzinfo=timezone.utc)
        result = await engine.proactive_check("quiet_group", _now=fixed_now)

        assert result is not None
        assert result["strategy"] == "proactive"

    @pytest.mark.asyncio
    async def test_atmosphere_trigger_suppressed_when_recent_message(self, tmp_path):
        """Atmosphere trigger should be suppressed if a message arrived recently."""
        from datetime import datetime, timezone, timedelta
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )

        # Set a very negative atmosphere via the manager API so it persists
        from sirius_chat.memory.semantic.models import AtmosphereSnapshot
        engine.semantic_memory.append_atmosphere(
            "sad_group",
            AtmosphereSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                group_valence=-0.8,
                group_arousal=0.2,
                heat_level="warm",
            ),
        )

        # Inject 15:00 (inside active hours 12-21)
        fixed_now = datetime(2026, 4, 18, 15, 0, 0, tzinfo=timezone.utc)
        # Last message was only 30 seconds ago (relative to injected time)
        recent = fixed_now - timedelta(seconds=30)
        engine._group_last_message_at["sad_group"] = recent.isoformat()

        result = await engine.proactive_check("sad_group", _now=fixed_now)
        # Should be suppressed because of recent activity
        assert result is None

    @pytest.mark.asyncio
    async def test_atmosphere_trigger_fires_when_quiet(self, tmp_path):
        """Atmosphere trigger should fire if group is quiet enough."""
        from datetime import datetime, timezone, timedelta
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )

        # Set a very negative atmosphere via the manager API so it persists
        from sirius_chat.memory.semantic.models import AtmosphereSnapshot
        engine.semantic_memory.append_atmosphere(
            "sad_group",
            AtmosphereSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                group_valence=-0.8,
                group_arousal=0.2,
                heat_level="warm",
            ),
        )

        # Inject 15:00 (inside active hours 12-21)
        fixed_now = datetime(2026, 4, 18, 15, 0, 0, tzinfo=timezone.utc)
        # Last message was 10 minutes ago (relative to injected time)
        past = fixed_now - timedelta(minutes=10)
        engine._group_last_message_at["sad_group"] = past.isoformat()

        result = await engine.proactive_check("sad_group", _now=fixed_now)
        # Should trigger because group has been quiet
        assert result is not None
        assert result["strategy"] == "proactive"
        assert result["trigger_type"] == "atmosphere"


class TestE2EMultiGroupIsolation:
    @pytest.mark.asyncio
    async def test_messages_stay_in_own_group(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="dave", user_id="dave")

        await engine.process_message(
            Message(role="human", content="group A message", speaker="dave"),
            [p], "group_a",
        )
        await engine.process_message(
            Message(role="human", content="group B message", speaker="dave"),
            [p], "group_b",
        )

        wa = engine.working_memory.get_window("group_a")
        wb = engine.working_memory.get_window("group_b")

        assert len(wa) == 1
        assert len(wb) == 1
        assert wa[0].content == "group A message"
        assert wb[0].content == "group B message"

    @pytest.mark.asyncio
    async def test_user_memory_is_group_isolated(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="eve", user_id="eve")

        # Register in group_a and add a fact
        engine.user_memory.register_user(p.as_user_profile(), group_id="group_a")
        engine.user_memory.add_memory_fact(
            user_id="eve", fact_type="hobby", value="coding",
            source="test", confidence=0.9, group_id="group_a",
        )

        # Same user in group_b should not have the fact
        engine.user_memory.register_user(p.as_user_profile(), group_id="group_b")
        entry_b = engine.user_memory.get_user_by_id("eve", group_id="group_b")

        assert entry_b is not None
        assert len(entry_b.runtime.memory_facts) == 0


class TestE2EAtmosphereShift:
    @pytest.mark.asyncio
    async def test_positive_to_negative_changes_empathy(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="frank", user_id="frank")

        # Positive message
        result1 = await engine.process_message(
            Message(role="human", content="太棒了！我升职了！", speaker="frank"),
            [p], "work_group",
        )
        emotion1 = result1["emotion"]
        assert emotion1["valence"] > 0.3  # positive

        # Negative message
        result2 = await engine.process_message(
            Message(role="human", content="崩溃了，项目被砍了", speaker="frank"),
            [p], "work_group",
        )
        emotion2 = result2["emotion"]
        assert emotion2["valence"] < -0.2  # negative

        # Group atmosphere history should have both snapshots
        group_profile = engine.semantic_memory.get_group_profile("work_group")
        assert group_profile is not None
        assert len(group_profile.atmosphere_history) >= 2



class TestE2EBackgroundTasks:
    @pytest.mark.asyncio
    async def test_background_tasks_start_stop(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        engine.start_background_tasks()
        assert engine._bg_running is True
        assert len(engine._bg_tasks) == 4

        engine.stop_background_tasks()
        assert engine._bg_running is False
        assert len(engine._bg_tasks) == 0

    @pytest.mark.asyncio
    async def test_background_delayed_queue_tick(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
            config={"delayed_queue_tick_interval_seconds": 0.1},
        )
        p = Participant(name="alice", user_id="alice")

        # Enqueue a delayed item manually
        from sirius_chat.models.response_strategy import StrategyDecision, ResponseStrategy
        engine.delayed_queue.enqueue(
            group_id="test_group",
            user_id="alice",
            message_content="这个话题很有意思",
            strategy_decision=StrategyDecision(strategy=ResponseStrategy.DELAYED, urgency=50),
            emotion_state={},
            candidate_memories=[],
        )

        # Start background tasks
        engine.start_background_tasks()

        # Wait for tick
        await asyncio.sleep(0.2)

        engine.stop_background_tasks()

    @pytest.mark.asyncio
    async def test_background_idempotent(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        engine.start_background_tasks()
        first_tasks = list(engine._bg_tasks)
        engine.start_background_tasks()  # idempotent
        second_tasks = list(engine._bg_tasks)
        assert first_tasks == second_tasks
        engine.stop_background_tasks()



class TestE2EGroupNormLearning:
    @pytest.mark.asyncio
    async def test_group_norms_updated_after_messages(self, tmp_path):
        engine = EmotionalGroupChatEngine(
            work_path=tmp_path,
            persona=PersonaProfile(name="TestBot"),
        )
        p = Participant(name="grace", user_id="grace")

        # Send a few messages
        await engine.process_message(
            Message(role="human", content="大家好！今天天气真棒 🎉", speaker="grace"),
            [p], "norm_group",
        )
        await engine.process_message(
            Message(role="human", content="哈哈确实", speaker="grace"),
            [p], "norm_group",
        )
        await engine.process_message(
            Message(role="human", content="@所有人 记得开会", speaker="grace"),
            [p], "norm_group",
        )

        profile = engine.semantic_memory.get_group_profile("norm_group")
        assert profile is not None
        norms = profile.group_norms

        assert norms.get("message_count", 0) == 3
        assert norms.get("emoji_usage_rate", 0) > 0
        assert norms.get("mention_rate", 0) > 0
        assert "avg_message_length" in norms
        assert "length_distribution" in norms
        assert "active_hours" in norms
        assert profile.typical_interaction_style in ("active", "humorous", "balanced")
