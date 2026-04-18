"""Tests for AutobiographicalMemoryManager."""

from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from sirius_chat.memory.autobiographical import AutobiographicalMemoryManager, SelfSemanticProfile
from sirius_chat.memory.autobiographical.manager import AutobiographicalMemoryManager as AMMgr
from sirius_chat.models.emotion import EmotionState
from sirius_chat.models.persona import PersonaProfile


class TestRecordThought:
    def test_empty_content_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            assert mgr.record_thought("") is None
            assert mgr.record_thought("   ") is None

    def test_records_thought_with_emotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            emotion = EmotionState(valence=0.5, arousal=0.6, intensity=0.8)
            entry = mgr.record_thought(
                "这用户挺有意思的", emotion=emotion, trigger_message="你好"
            )
            assert entry is not None
            assert entry.content == "这用户挺有意思的"
            assert entry.importance > 0.5  # emotion boost
            assert entry.category == "reflection"

    def test_recent_thoughts_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            mgr.record_thought("想法1")
            mgr.record_thought("想法2")
            recent = mgr.get_recent_thoughts(n=5)
            assert len(recent) == 2
            assert recent[-1]["content"] == "想法2"

    def test_emotion_timeline_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            emotion = EmotionState(valence=-0.3, arousal=0.4)
            mgr.record_thought("有点担心", emotion=emotion)
            timeline = mgr.get_emotion_timeline(n=5)
            assert len(timeline) == 1
            assert timeline[0]["valence"] == pytest.approx(-0.3, 0.01)


class TestValueWeightedImportance:
    def test_value_resonance_boosts_importance(self):
        persona = PersonaProfile(
            name="Test",
            core_values=["真诚", "正义"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp, persona=persona)
            entry = mgr.record_thought("我觉得真诚很重要，正义也不能少")
            assert entry is not None
            # Two value hits -> +0.2
            assert entry.importance > 0.6

    def test_no_persona_gives_base_importance(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            entry = mgr.record_thought("普通想法")
            assert entry is not None
            assert entry.importance == pytest.approx(0.5, 0.01)


class TestSelfSemanticProfile:
    def test_record_emotion_bounds_timeline(self):
        profile = SelfSemanticProfile()
        for i in range(10):
            profile.record_emotion(valence=0.1 * i, arousal=0.2)
        assert len(profile.emotion_timeline) == 10

        for i in range(500):
            profile.record_emotion(valence=0.0, arousal=0.0)
        assert len(profile.emotion_timeline) == 500

    def test_reinforce_value_caps_at_one(self):
        profile = SelfSemanticProfile()
        for _ in range(30):
            profile.reinforce_value("test_value", delta=0.05)
        assert profile.value_weights["test_value"] == 1.0

    def test_to_dict_roundtrip(self):
        profile = SelfSemanticProfile(
            self_description="我是小暖",
            core_values=["温暖", "真诚"],
        )
        profile.record_emotion(0.5, 0.3)
        data = profile.to_dict()
        restored = SelfSemanticProfile.from_dict(data)
        assert restored.self_description == "我是小暖"
        assert restored.core_values == ["温暖", "真诚"]
        assert len(restored.emotion_timeline) == 1


class TestPromptSections:
    def test_build_self_prompt_section_with_data(self):
        persona = PersonaProfile(name="小暖", core_values=["温暖"])
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp, persona=persona)
            mgr.record_thought("测试", emotion=EmotionState(valence=0.5, arousal=0.3))
            section = mgr.build_self_prompt_section()
            assert "我是谁" in section
            assert "小暖" in section
            assert "温暖" in section

    def test_build_diary_prompt_section_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            section = mgr.build_diary_prompt_section()
            assert section == ""

    def test_build_diary_prompt_section_with_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            mgr.record_experience("今天认识了新朋友", category="milestone")
            section = mgr.build_diary_prompt_section()
            assert "新朋友" in section


class TestPersistence:
    def test_save_and_load_diary(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = AMMgr(tmp)
            mgr.record_thought("持久化测试")
            mgr.save()

            # New manager loading same path
            mgr2 = AMMgr(tmp)
            entries = mgr2.get_relevant_diary_entries(max_entries=10)
            assert len(entries) == 1
            assert entries[0].content == "持久化测试"
