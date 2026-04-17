"""Integration tests for persona system in EmotionalGroupChatEngine."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from sirius_chat.models.persona import PersonaProfile
from sirius_chat.core.persona_generator import PersonaGenerator
from sirius_chat.core.persona_store import PersonaStore
from sirius_chat.core.response_assembler import ResponseAssembler, StyleAdapter
from sirius_chat.models.emotion import EmotionState, EmpathyStrategy
from sirius_chat.models.models import Message


class TestPersonaProfileRoundtrip:
    def test_to_dict_from_dict_identity(self):
        p = PersonaProfile(
            name="TestBot",
            aliases=["TB"],
            persona_summary="A test bot",
            personality_traits=["friendly", "helpful"],
            catchphrases=["Got it!"],
            emotional_baseline={"valence": 0.5, "arousal": 0.4},
            reply_frequency="high",
        )
        data = p.to_dict()
        p2 = PersonaProfile.from_dict(data)
        assert p2.name == "TestBot"
        assert p2.aliases == ["TB"]
        assert p2.personality_traits == ["friendly", "helpful"]
        assert p2.catchphrases == ["Got it!"]
        assert p2.emotional_baseline == {"valence": 0.5, "arousal": 0.4}
        assert p2.reply_frequency == "high"


class TestTemplatePersonaCreation:
    def test_all_archetypes_valid(self):
        archetypes = [
            "warm_friend", "sarcastic_techie", "gentle_caregiver",
            "chaotic_jester", "stoic_observer", "protective_elder",
        ]
        for name in archetypes:
            p = PersonaGenerator.from_template(name)
            assert p.name
            assert p.personality_traits
            assert p.communication_style
            assert p.source == "template"

    def test_unknown_archetype_raises(self):
        with pytest.raises(ValueError):
            PersonaGenerator.from_template("nonexistent")


class TestKeywordPersonaGeneration:
    def test_keyword_mapping_applies(self):
        p = PersonaGenerator.from_keywords("测试", ["毒舌", "程序员", "乐观"])
        assert p.name == "测试"
        assert "毒舌" in p.personality_traits
        assert "逻辑强" in p.personality_traits
        assert p.humor_style == "sarcastic"
        assert p.communication_style == "concise"
        assert p.emotional_baseline["valence"] == 0.6

    def test_unknown_keywords_ignored(self):
        p = PersonaGenerator.from_keywords("测试", ["完全不存在的词"])
        assert p.name == "测试"
        # Should still produce a valid profile with defaults
        assert p.source == "keyword"


class TestResponseAssemblerPersonaInjection:
    def test_prompt_contains_persona_name(self):
        persona = PersonaGenerator.from_template("sarcastic_techie")
        assembler = ResponseAssembler(persona=persona)
        prompt = assembler.assemble(
            message=Message(role="human", content="你好"),
            intent=Mock(),
            emotion=EmotionState(),
            empathy_strategy=EmpathyStrategy(strategy_type="presence", priority=3, depth_level=1),
            memories=[],
            group_profile=None,
            user_profile=None,
            assistant_emotion=Mock(valence=0.0, arousal=0.0),
        )
        assert persona.name in prompt
        assert "毒舌" in prompt or "机智" in prompt

    def test_default_prompt_without_persona(self):
        assembler = ResponseAssembler()
        prompt = assembler.assemble(
            message=Message(role="human", content="你好"),
            intent=Mock(),
            emotion=EmotionState(),
            empathy_strategy=EmpathyStrategy(strategy_type="presence", priority=3, depth_level=1),
            memories=[],
            group_profile=None,
            user_profile=None,
            assistant_emotion=Mock(valence=0.0, arousal=0.0),
        )
        assert "你在一个多人聊天场景里" in prompt


class TestEngineLoadsPersona:
    def test_engine_creates_default_persona(self, tmp_path):
        from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
        engine = EmotionalGroupChatEngine(work_path=tmp_path)
        assert engine.persona is not None
        assert engine.persona.name == "小暖"  # warm_friend default
        # Verify saved to disk
        loaded = PersonaStore.load(tmp_path)
        assert loaded is not None
        assert loaded.name == "小暖"

    def test_engine_loads_existing_persona(self, tmp_path):
        from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
        custom = PersonaGenerator.from_template("stoic_observer")
        PersonaStore.save(tmp_path, custom)

        engine = EmotionalGroupChatEngine(work_path=tmp_path)
        assert engine.persona.name == "静观"

    def test_engine_accepts_custom_persona(self, tmp_path):
        from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
        custom = PersonaProfile(name="CustomBot", reply_frequency="low")
        engine = EmotionalGroupChatEngine(work_path=tmp_path, persona=custom)
        assert engine.persona.name == "CustomBot"
        assert engine.persona.reply_frequency == "low"


class TestPersonaBiasesThreshold:
    def test_high_frequency_lowers_threshold(self, tmp_path):
        from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
        from sirius_chat.models.intent_v3 import IntentAnalysisV3
        from sirius_chat.models.emotion import EmotionState

        high_p = PersonaProfile(name="Chatty", reply_frequency="high")
        engine = EmotionalGroupChatEngine(work_path=tmp_path, persona=high_p)

        intent = IntentAnalysisV3(urgency_score=30, relevance_score=0.4)
        emotion = EmotionState()

        decision = engine._decision(intent, emotion, "g1", "u1")
        # high frequency should make it easier to reply (lower threshold)
        assert intent.threshold < 0.6  # default base is ~0.45, high *0.8 = ~0.36

    def test_low_frequency_raises_threshold(self, tmp_path):
        from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
        from sirius_chat.models.intent_v3 import IntentAnalysisV3
        from sirius_chat.models.emotion import EmotionState

        low_p = PersonaProfile(name="Quiet", reply_frequency="low")
        engine = EmotionalGroupChatEngine(work_path=tmp_path, persona=low_p)

        intent = IntentAnalysisV3(urgency_score=30, relevance_score=0.4)
        emotion = EmotionState()

        decision = engine._decision(intent, emotion, "g1", "u1")
        # low frequency should make it harder to reply (higher threshold)
        assert intent.threshold > 0.5


class TestStyleAdapterPersonaPrefs:
    def test_persona_max_tokens_override(self):
        adapter = StyleAdapter()
        persona = PersonaProfile(max_tokens_preference=60, communication_style="concise")
        style = adapter.adapt(
            heat_level="warm", pace="steady", persona=persona
        )
        assert style.max_tokens <= 60
        assert "简洁" in style.length_instruction

    def test_persona_temperature_override(self):
        adapter = StyleAdapter()
        persona = PersonaProfile(temperature_preference=0.9, communication_style="casual")
        style = adapter.adapt(
            heat_level="warm", pace="steady", persona=persona
        )
        assert style.temperature == 0.9


class TestPersonaPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        p = PersonaProfile(name="PersistBot", catchphrases=["Yo!"])
        PersonaStore.save(tmp_path, p)
        loaded = PersonaStore.load(tmp_path)
        assert loaded is not None
        assert loaded.name == "PersistBot"
        assert loaded.catchphrases == ["Yo!"]

    def test_load_missing_returns_none(self, tmp_path):
        loaded = PersonaStore.load(tmp_path)
        assert loaded is None
