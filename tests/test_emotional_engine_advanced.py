"""Advanced unit tests for v0.28 memory and core subsystems."""

from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone, timedelta

# ── activation engine ──
from sirius_chat.memory.activation_engine import ActivationEngine, DecaySchedule

# ── retrieval engine ──
from sirius_chat.memory.retrieval_engine import MemoryRetriever
from sirius_chat.memory.working.manager import WorkingMemoryManager
from sirius_chat.memory.episodic.manager import EpisodicMemoryManager
from sirius_chat.memory.semantic.manager import SemanticMemoryManager
from sirius_chat.memory.event.models import EventMemoryEntry

# ── threshold engine ──
from sirius_chat.core.threshold_engine import ThresholdEngine
from sirius_chat.memory.semantic.models import RelationshipState

# ── user manager (group isolation) ──
from sirius_chat.memory.user.manager import UserMemoryManager
from sirius_chat.memory.user.models import UserProfile, MemoryFact


# ============================================================================
# ActivationEngine
# ============================================================================

class TestActivationEngine:
    def test_default_activation_equals_importance(self):
        """Fresh entry (no decay yet) should have activation ~= importance."""
        engine = ActivationEngine()
        now = datetime.now(timezone.utc).isoformat()
        act = engine.calculate_activation(
            importance=0.8,
            created_at=now,
            access_count=0,
            memory_category="custom",
        )
        # Within 1% of importance (no time has passed)
        assert pytest.approx(act, 0.01) == 0.8

    def test_access_boost_increases_activation(self):
        engine = ActivationEngine()
        now = datetime.now(timezone.utc).isoformat()
        act0 = engine.calculate_activation(0.5, now, 0, "custom")
        act3 = engine.calculate_activation(0.5, now, 3, "custom")
        assert act3 > act0
        # 1 + 0.1*3 = 1.3x boost
        assert pytest.approx(act3 / act0, 0.01) == 1.3

    def test_time_decay_reduces_activation(self):
        engine = ActivationEngine()
        old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        act = engine.calculate_activation(1.0, old, 0, "custom")
        assert act < 1.0
        # lambda=0.01, 100h -> exp(-1) ≈ 0.368
        assert pytest.approx(act, 0.05) == math.exp(-1.0)

    def test_category_lambda_differentiation(self):
        engine = ActivationEngine()
        old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
        # identity/preference has lambda=0.001 (slow decay)
        act_identity = engine.calculate_activation(1.0, old, 0, "identity")
        # emotion has lambda=0.05 (fast decay)
        act_emotion = engine.calculate_activation(1.0, old, 0, "emotion")
        assert act_identity > act_emotion

    def test_archive_threshold(self):
        engine = ActivationEngine()
        assert engine.should_archive(0.05) is True
        assert engine.should_archive(0.15) is False

    def test_on_access_returns_incremented_count(self):
        engine = ActivationEngine()
        now = datetime.now(timezone.utc).isoformat()
        new_act, new_count = engine.on_access(0.6, now, 2, "custom")
        assert new_count == 3
        assert new_act > 0.0

    def test_recalculate_all_batch(self):
        engine = ActivationEngine()
        now = datetime.now(timezone.utc).isoformat()
        items = [
            {"importance": 0.5, "created_at": now, "access_count": 0, "memory_category": "custom"},
            {"importance": 0.5, "created_at": now, "access_count": 5, "memory_category": "custom"},
        ]
        results = engine.recalculate_all(items)
        assert len(results) == 2
        assert "activation" in results[0]
        assert results[1]["activation"] > results[0]["activation"]  # more accesses

    def test_hours_since_malformed_returns_none(self):
        assert ActivationEngine._hours_since("") is None
        assert ActivationEngine._hours_since("not-a-date") is None


# ============================================================================
# MemoryRetriever
# ============================================================================

class TestMemoryRetriever:
    def test_working_memory_search(self, tmp_path):
        wm = WorkingMemoryManager()
        wm.add_entry("g1", "u1", "human", "I love Python programming", importance=0.8)
        wm.add_entry("g1", "u2", "human", "Let's talk about cooking", importance=0.5)

        retriever = MemoryRetriever(working_mgr=wm)
        results = retriever._search_working_memory("g1", "Python", None)
        assert len(results) == 1
        assert results[0]["content"] == "I love Python programming"
        assert results[0]["source"] == "working_memory"

    def test_working_memory_search_with_user_filter(self, tmp_path):
        wm = WorkingMemoryManager()
        wm.add_entry("g1", "u1", "human", "hello world", importance=0.5)
        wm.add_entry("g1", "u2", "human", "hello world", importance=0.5)

        retriever = MemoryRetriever(working_mgr=wm)
        results = retriever._search_working_memory("g1", "hello", "u1")
        assert len(results) == 1
        assert results[0]["user_id"] == "u1"

    def test_episodic_keyword_search(self, tmp_path):
        em = EpisodicMemoryManager(tmp_path)
        entry = EventMemoryEntry(
            event_id="e1",
            user_id="u1",
            group_id="g1",
            category="preference",
            summary="User enjoys hiking in mountains",
            confidence=0.9,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        em.add_entry(entry)

        retriever = MemoryRetriever(episodic_mgr=em)
        results = retriever._search_episodic_keywords("g1", "hiking", None)
        assert len(results) == 1
        assert results[0]["content"] == "User enjoys hiking in mountains"
        assert results[0]["source"] == "episodic_memory"

    def test_deduplicate_and_score(self):
        retriever = MemoryRetriever()
        results = [
            {"content": "hello", "importance": 0.8, "activation": 1.0, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"content": "hello", "importance": 0.5, "activation": 0.5, "timestamp": datetime.now(timezone.utc).isoformat()},
            {"content": "world", "importance": 0.6, "activation": 0.8, "timestamp": datetime.now(timezone.utc).isoformat()},
        ]
        deduped = retriever._deduplicate_and_score(results, "test", 5)
        assert len(deduped) == 2  # "hello" deduplicated
        assert all("score" in r for r in deduped)

    def test_user_profile_search(self, tmp_path):
        from sirius_chat.memory.semantic.models import InterestNode
        sm = SemanticMemoryManager(work_path=tmp_path)
        sm.update_user_profile("g1", "u1", updates={"base_attributes": {"hobby": "photography", "job": "engineer"}})
        sm.update_user_profile("g1", "u1", updates={"interest_graph": [InterestNode(topic="travel", participation=0.8)]})

        retriever = MemoryRetriever(semantic_mgr=sm)
        results = retriever._search_user_profile("g1", "u1", "photo")
        assert len(results) >= 1
        assert any("photography" in r["content"] for r in results)

    def test_retrieve_integration(self, tmp_path):
        wm = WorkingMemoryManager()
        wm.add_entry("g1", "u1", "human", "Python is great", importance=0.9)

        retriever = MemoryRetriever(working_mgr=wm)
        # retrieve is async but doesn't need event loop for working-memory only
        import asyncio
        results = asyncio.run(retriever.retrieve("Python", "g1"))
        assert len(results) >= 1
        assert results[0]["source"] == "working_memory"


# ============================================================================
# ThresholdEngine
# ============================================================================

class TestThresholdEngine:
    def test_base_threshold_sensitivity(self):
        engine = ThresholdEngine(base_low=0.2, base_high=0.8)
        # Fix hour to noon so time_factor is deterministic (1.1)
        # activity_factor("warm", 0) = 0.9 (low message rate penalty)
        t_high = engine.compute(sensitivity=0.0, hour_of_day=12)
        t_low = engine.compute(sensitivity=1.0, hour_of_day=12)
        assert t_high > t_low
        assert pytest.approx(t_high, 0.01) == 0.8 * 0.9 * 1.1  # base * activity * time
        assert pytest.approx(t_low, 0.01) == 0.2 * 0.9 * 1.1

    def test_activity_factor_heat_levels(self):
        # At 0 msg/min, low rate penalty (-0.1) applies for all
        assert ThresholdEngine._activity_factor("cold", 0) == pytest.approx(0.7, 0.01)
        assert ThresholdEngine._activity_factor("warm", 0) == pytest.approx(0.9, 0.01)
        assert ThresholdEngine._activity_factor("hot", 0) == pytest.approx(1.2, 0.01)
        assert ThresholdEngine._activity_factor("overheated", 0) == pytest.approx(1.5, 0.01)

    def test_activity_factor_message_rate_adjustment(self):
        # High message rate (>6/min) boosts
        assert ThresholdEngine._activity_factor("warm", 10) == pytest.approx(1.2, 0.01)
        # Low message rate (<0.5/min) reduces
        assert ThresholdEngine._activity_factor("warm", 0.1) == pytest.approx(0.9, 0.01)

    def test_relationship_factor_familiarity(self):
        state = RelationshipState()
        state.interaction_frequency_7d = 20.0
        state.emotional_intimacy = 1.0
        state.trust_score = 1.0
        state.dependency_score = 1.0
        fam = state.compute_familiarity()
        assert fam >= 0.9
        factor = ThresholdEngine._relationship_factor(state)
        assert factor == pytest.approx(0.6, 0.01)

    def test_relationship_factor_none(self):
        assert ThresholdEngine._relationship_factor(None) == 1.0

    def test_time_factor_night(self):
        # Midnight hours (0-6) have higher threshold (less engagement expected)
        assert ThresholdEngine._time_factor(3) == pytest.approx(1.3, 0.01)

    def test_time_factor_work_hours(self):
        assert ThresholdEngine._time_factor(12) == pytest.approx(1.1, 0.01)

    def test_time_factor_evening(self):
        assert ThresholdEngine._time_factor(20) == pytest.approx(0.9, 0.01)

    def test_threshold_bounds(self):
        engine = ThresholdEngine()
        t = engine.compute(sensitivity=0.5, heat_level="overheated", messages_per_minute=10)
        assert 0.1 <= t <= 0.9


# ============================================================================
# UserMemoryManager group isolation
# ============================================================================

class TestUserMemoryManagerGroupIsolation:
    def test_register_user_per_group(self):
        mgr = UserMemoryManager()
        p1 = UserProfile(user_id="u1", name="Alice")
        mgr.register_user(p1, group_id="g1")
        mgr.register_user(p1, group_id="g2")

        assert "u1" in mgr.entries["g1"]
        assert "u1" in mgr.entries["g2"]
        # They are independent entries
        assert mgr.entries["g1"]["u1"] is not mgr.entries["g2"]["u1"]

    def test_get_user_by_id_group_scoped(self):
        mgr = UserMemoryManager()
        p1 = UserProfile(user_id="u1", name="Alice")
        mgr.register_user(p1, group_id="g1")

        assert mgr.get_user_by_id("u1", group_id="g1") is not None
        assert mgr.get_user_by_id("u1", group_id="g2") is None

    def test_add_memory_fact_group_scoped(self):
        mgr = UserMemoryManager()
        p1 = UserProfile(user_id="u1", name="Alice")
        mgr.register_user(p1, group_id="g1")
        mgr.register_user(p1, group_id="g2")

        mgr.add_memory_fact(
            user_id="u1",
            fact_type="hobby",
            value="painting",
            source="test",
            confidence=0.9,
            group_id="g1",
        )

        g1_facts = mgr.get_user_by_id("u1", "g1").runtime.memory_facts
        g2_facts = mgr.get_user_by_id("u1", "g2").runtime.memory_facts
        assert len(g1_facts) == 1
        assert len(g2_facts) == 0

    def test_merge_from_preserves_groups(self):
        mgr_a = UserMemoryManager()
        mgr_b = UserMemoryManager()

        mgr_a.register_user(UserProfile(user_id="u1", name="Alice"), group_id="g1")
        mgr_b.register_user(UserProfile(user_id="u2", name="Bob"), group_id="g2")

        mgr_a.merge_from(mgr_b)
        assert "u1" in mgr_a.entries["g1"]
        assert "u2" in mgr_a.entries["g2"]

    def test_search_users_by_fact_cross_group(self):
        mgr = UserMemoryManager()
        for gid in ["g1", "g2"]:
            mgr.register_user(UserProfile(user_id="u1", name="Alice"), group_id=gid)
            mgr.add_memory_fact(
                user_id="u1",
                fact_type="hobby",
                value="gaming",
                source="test",
                confidence=0.8,
                group_id=gid,
            )

        # Search all groups
        all_results = mgr.search_users_by_fact("hobby", value="gaming")
        assert len(all_results) == 1  # same user_id aggregated

        # Search specific group
        g1_results = mgr.search_users_by_fact("hobby", value="gaming", group_id="g1")
        assert len(g1_results) == 1

    def test_serialization_roundtrip(self):
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id="u1", name="Alice"), group_id="g1")
        mgr.add_memory_fact(
            user_id="u1",
            fact_type="trait",
            value="friendly",
            source="test",
            confidence=0.9,
            group_id="g1",
        )

        data = mgr.to_dict()
        restored = UserMemoryManager.from_dict(data)

        assert "g1" in restored.entries
        assert "u1" in restored.entries["g1"]
        assert len(restored.entries["g1"]["u1"].runtime.memory_facts) == 1
        assert restored.entries["g1"]["u1"].profile.name == "Alice"

    def test_cleanup_expired_transient_facts(self):
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id="u1", name="Alice"), group_id="g1")
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        mgr.add_memory_fact(
            user_id="u1",
            fact_type="mood",
            value="happy",
            source="test",
            confidence=0.5,  # below transient threshold of 0.85
            observed_at=old_time,
            group_id="g1",
        )
        deleted = mgr.cleanup_expired_transient_facts("u1", max_age_minutes=30, group_id="g1")
        assert deleted == 1
        assert len(mgr.get_user_by_id("u1", "g1").runtime.memory_facts) == 0

    def test_memory_fact_deduplication(self):
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id="u1", name="Alice"), group_id="g1")
        mgr.add_memory_fact(
            user_id="u1",
            fact_type="hobby",
            value="reading",
            source="test1",
            confidence=0.7,
            group_id="g1",
        )
        mgr.add_memory_fact(
            user_id="u1",
            fact_type="hobby",
            value="reading",
            source="test2",
            confidence=0.9,  # higher confidence
            group_id="g1",
        )
        facts = mgr.get_user_by_id("u1", "g1").runtime.memory_facts
        assert len(facts) == 1
        assert facts[0].confidence == pytest.approx(0.9, 0.01)
        assert facts[0].mention_count == 2

    def test_compress_memory_facts(self):
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id="u1", name="Alice"), group_id="g1")
        for i in range(15):
            mgr.add_memory_fact(
                user_id="u1",
                fact_type="interest",
                value=f"topic_{i}",
                source="test",
                confidence=0.5 + i * 0.03,
                group_id="g1",
            )
        deleted = mgr.compress_memory_facts("u1", group_id="g1")
        assert deleted > 0
        assert len(mgr.get_user_by_id("u1", "g1").runtime.memory_facts) < 15

    def test_get_rich_user_summary(self):
        mgr = UserMemoryManager()
        mgr.register_user(UserProfile(user_id="u1", name="Alice", persona="cheerful"), group_id="g1")
        mgr.add_memory_fact(
            user_id="u1",
            fact_type="hobby",
            value="painting",
            source="test",
            confidence=0.9,
            group_id="g1",
        )
        summary = mgr.get_rich_user_summary("u1", group_id="g1")
        assert summary["name"] == "Alice"
        assert summary["persona"] == "cheerful"
        assert "hobby" in summary["facts_by_type"]

    def test_trait_normalization(self):
        mgr = UserMemoryManager()
        # "社交" is in the Social category keywords
        assert mgr._normalize_trait("社交") != "社交"  # should map to taxonomy category
        assert mgr._normalize_trait("xyz_unknown_trait_xyz") == "xyz_unknown_trait_xyz"
