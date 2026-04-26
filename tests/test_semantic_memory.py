"""Tests for SemanticMemoryManager and SemanticProfileStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sirius_chat.memory.semantic.manager import SemanticMemoryManager
from sirius_chat.memory.semantic.models import (
    AtmosphereSnapshot,
    GroupSemanticProfile,
    RelationshipState,
    UserSemanticProfile,
)


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmp:
        yield SemanticMemoryManager(tmp)


# ==================================================================
# Group profile persistence
# ==================================================================

class TestGroupProfilePersistence:
    def test_ensure_group_profile_creates_default(self, manager):
        profile = manager.ensure_group_profile("g1")
        assert isinstance(profile, GroupSemanticProfile)
        assert profile.group_id == "g1"

    def test_group_profile_persists_to_disk(self, manager):
        profile = manager.ensure_group_profile("g1")
        profile.group_norms["test_key"] = "test_value"
        manager.save_group_profile("g1")

        # New manager should load from disk
        manager2 = SemanticMemoryManager(manager._store._base.parent.parent)
        loaded = manager2.ensure_group_profile("g1")
        assert loaded.group_norms.get("test_key") == "test_value"

    def test_atmosphere_history_limit(self, manager):
        for i in range(110):
            manager.record_atmosphere("g1", valence=0.1, arousal=0.2, active_participants=3)
        profile = manager.ensure_group_profile("g1")
        assert len(profile.atmosphere_history) == 100
        assert profile.atmosphere_history[-1].group_valence == 0.1


# ==================================================================
# User profile persistence
# ==================================================================

class TestUserProfilePersistence:
    def test_get_user_profile_creates_default(self, manager):
        profile = manager.get_user_profile("g1", "u1")
        assert isinstance(profile, UserSemanticProfile)
        assert profile.user_id == "u1"

    def test_user_profile_persists_to_disk(self, manager):
        profile = manager.get_user_profile("g1", "u1")
        profile.communication_style = "casual"
        manager.save_user_profile("g1", "u1")

        manager2 = SemanticMemoryManager(manager._store._base.parent.parent)
        loaded = manager2.get_user_profile("g1", "u1")
        assert loaded.communication_style == "casual"

    def test_list_group_user_profiles(self, manager):
        manager.get_user_profile("g1", "u1")
        manager.get_user_profile("g1", "u2")
        manager.save_user_profile("g1", "u1")
        manager.save_user_profile("g1", "u2")

        profiles = manager.list_group_user_profiles("g1")
        assert len(profiles) == 2
        user_ids = {p.user_id for p in profiles}
        assert user_ids == {"u1", "u2"}


# ==================================================================
# Passive learning (group norms)
# ==================================================================

class TestPassiveLearning:
    def test_learn_message_increments_count(self, manager):
        manager.learn_from_message("g1", "hello world", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.group_norms.get("message_count") == 1

    def test_learn_multiple_messages(self, manager):
        for i in range(5):
            manager.learn_from_message("g1", f"msg {i}", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.group_norms.get("message_count") == 5

    def test_emoji_detection(self, manager):
        manager.learn_from_message("g1", "hello 😊", social_intent="chat")
        manager.learn_from_message("g1", "no emoji", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.group_norms.get("emoji_total") == 1
        assert profile.group_norms.get("emoji_usage_rate") == 0.5

    def test_mention_detection(self, manager):
        manager.learn_from_message("g1", "@user hello", social_intent="chat")
        manager.learn_from_message("g1", "plain text", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.group_norms.get("mention_total") == 1
        assert profile.group_norms.get("mention_rate") == 0.5

    def test_interaction_style_active(self, manager):
        for _ in range(10):
            manager.learn_from_message("g1", "ok", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.typical_interaction_style == "active"

    def test_topic_switch_tracking(self, manager):
        manager.learn_from_message("g1", "a", social_intent="chat")
        manager.learn_from_message("g1", "b", social_intent="help")
        manager.learn_from_message("g1", "c", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        assert profile.group_norms.get("topic_switches") == 2

    def test_keyword_extraction_no_longer_in_passive_learning(self, manager):
        # Interest topics are now extracted via LLM in DiaryGenerator.
        # Passive learning only tracks message stats.
        for _ in range(5):
            manager.learn_from_message("g1", "python asyncio", social_intent="chat")
        profile = manager.ensure_group_profile("g1")
        # interest_topics and dominant_topic should be empty (set by LLM only)
        assert profile.interest_topics == []
        assert profile.dominant_topic == ""


# ==================================================================
# Atmosphere recording
# ==================================================================

class TestAtmosphereRecording:
    def test_record_atmosphere_appends(self, manager):
        manager.record_atmosphere("g1", valence=0.5, arousal=0.3, active_participants=2)
        profile = manager.ensure_group_profile("g1")
        assert len(profile.atmosphere_history) == 1
        snap = profile.atmosphere_history[0]
        assert isinstance(snap, AtmosphereSnapshot)
        assert snap.group_valence == 0.5
        assert snap.group_arousal == 0.3
        assert snap.active_participants == 2


# ==================================================================
# Relationship updates
# ==================================================================

class TestRelationshipUpdates:
    def test_update_relationship_basic(self, manager):
        manager.update_relationship("g1", "u1", valence=0.5, urgency_score=50, social_intent="chat")
        profile = manager.get_user_profile("g1", "u1")
        rs = profile.relationship_state
        assert rs.interaction_frequency_7d > 0
        assert rs.emotional_intimacy > 0
        assert rs.first_interaction_at != ""
        assert rs.last_interaction_at != ""

    def test_familiarity_grows_with_interactions(self, manager):
        for _ in range(10):
            manager.update_relationship("g1", "u1", valence=0.8, urgency_score=50, social_intent="chat")
        profile = manager.get_user_profile("g1", "u1")
        fam = profile.relationship_state.compute_familiarity()
        assert fam > 0.5

    def test_trust_from_high_urgency(self, manager):
        manager.update_relationship("g1", "u1", valence=0.5, urgency_score=80, social_intent="help")
        profile = manager.get_user_profile("g1", "u1")
        assert profile.relationship_state.trust_score > 0.5

    def test_dependency_from_help_intent(self, manager):
        manager.update_relationship("g1", "u1", valence=0.5, urgency_score=50, social_intent="help_request")
        profile = manager.get_user_profile("g1", "u1")
        assert profile.relationship_state.dependency_score > 0.5


# ==================================================================
# Integration: proactive topic selection
# ==================================================================

class TestProactiveTopicSelection:
    def test_pick_topic_from_interest_topics(self, manager):
        profile = manager.ensure_group_profile("g1")
        profile.interest_topics = ["gaming", "music"]
        manager.save_group_profile("g1")

        # Simulate _pick_proactive_topic logic
        group_profile = manager.get_group_profile("g1")
        candidates = list(group_profile.interest_topics)
        assert "gaming" in candidates

    def test_pick_topic_from_dominant_topic(self, manager):
        # dominant_topic is set by LLM during diary generation
        profile = manager.ensure_group_profile("g1")
        profile.dominant_topic = "artificial intelligence"
        manager.save_group_profile("g1")
        loaded = manager.ensure_group_profile("g1")
        assert loaded.dominant_topic == "artificial intelligence"

    def test_user_level_interests(self, manager):
        profile = manager.get_user_profile("g1", "u1")
        profile.interest_graph = [{"topic": "coding", "participation": 0.5}]
        manager.save_user_profile("g1", "u1")

        loaded = manager.get_user_profile("g1", "u1")
        assert len(loaded.interest_graph) == 1
