"""Tests for EngineStateStore and EmotionalGroupChatEngine persistence."""

from __future__ import annotations

import pytest

from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.core.engine_persistence import EngineStateStore
from sirius_chat.models.models import Message, Participant


class TestEngineStateStore:
    def test_save_and_load_working_memory(self, tmp_path):
        store = EngineStateStore(tmp_path)
        store.save_working_memory("group_a", [
            {"user_id": "u1", "content": "hello", "importance": 0.8},
        ])
        loaded = store.load_working_memory("group_a")
        assert len(loaded) == 1
        assert loaded[0]["content"] == "hello"

    def test_load_missing_group_returns_empty(self, tmp_path):
        store = EngineStateStore(tmp_path)
        assert store.load_working_memory("nonexistent") == []

    def test_save_and_load_assistant_emotion(self, tmp_path):
        store = EngineStateStore(tmp_path)
        state = {"valence": 0.5, "arousal": 0.3}
        store.save_assistant_emotion(state)
        loaded = store.load_assistant_emotion()
        assert loaded == state

    def test_load_missing_emotion_returns_none(self, tmp_path):
        store = EngineStateStore(tmp_path)
        assert store.load_assistant_emotion() is None

    def test_save_and_load_delayed_queue(self, tmp_path):
        store = EngineStateStore(tmp_path)
        store.save_delayed_queue([{"item_id": "d1", "content": "test"}])
        loaded = store.load_delayed_queue()
        assert len(loaded) == 1
        assert loaded[0]["item_id"] == "d1"

    def test_save_and_load_group_timestamps(self, tmp_path):
        store = EngineStateStore(tmp_path)
        store.save_group_timestamps({"group_a": "2026-04-17T10:00:00"})
        loaded = store.load_group_timestamps()
        assert loaded["group_a"] == "2026-04-17T10:00:00"

    def test_load_all(self, tmp_path):
        store = EngineStateStore(tmp_path)
        store.save_working_memory("g1", [{"user_id": "u1", "content": "hi"}])
        store.save_assistant_emotion({"valence": 0.2})
        store.save_delayed_queue([])
        store.save_group_timestamps({"g1": "2026-04-17T10:00:00"})

        state = store.load_all()
        assert "g1" in state["working_memories"]
        assert state["assistant_emotion"]["valence"] == 0.2
        assert state["group_timestamps"]["g1"] == "2026-04-17T10:00:00"


class TestEnginePersistenceIntegration:
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, tmp_path):
        engine = EmotionalGroupChatEngine(work_path=tmp_path)

        # Process some messages
        p = Participant(name="u1", user_id="u1")
        await engine.process_message(
            Message(role="human", content="hello", speaker="u1"),
            [p], "group_a",
        )
        await engine.process_message(
            Message(role="human", content="world", speaker="u1"),
            [p], "group_a",
        )

        # Save
        engine.save_state()

        # New engine, load state
        engine2 = EmotionalGroupChatEngine(work_path=tmp_path)
        engine2.load_state()

        # Verify working memory restored
        entries = engine2.working_memory.get_window("group_a")
        assert len(entries) == 2
        assert entries[0].content == "hello"
        assert entries[1].content == "world"

        # Verify group timestamp restored
        assert "group_a" in engine2._group_last_message_at

    def test_load_state_no_crash_when_empty(self, tmp_path):
        engine = EmotionalGroupChatEngine(work_path=tmp_path)
        engine.load_state()  # Should not crash when no prior state exists
        assert engine.working_memory.list_groups() == []
