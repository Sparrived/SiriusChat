"""
Memory System V2 测试 - 验证破坏性改造后的新功能

覆盖范围：
- MemoryFact: confidence clamping, dynamic is_transient(), mention_count, source_event_id
- MemoryPolicy: 配置化阈值、decay schedule、observed set limits
- UserMemoryManager: 去重提频、摘要限长、observed set capping
- 序列化: 新字段完整持久化与向后兼容
"""

import pytest
from datetime import datetime, timedelta, timezone
from sirius_chat.memory import UserMemoryManager, UserProfile
from sirius_chat.memory.user.models import MemoryFact
from sirius_chat.config import MemoryPolicy, OrchestrationPolicy
from sirius_chat.memory.user.store import UserMemoryFileStore


def _add(m, uid, ft, val, src, conf, **kw):
    """Shorthand for keyword-only add_memory_fact."""
    m.add_memory_fact(user_id=uid, fact_type=ft, value=val, source=src, confidence=conf, **kw)


# ---- MemoryFact model tests ----

class TestMemoryFactV2:
    """MemoryFact 新行为验证"""

    def test_confidence_clamped_to_0_1(self):
        f = MemoryFact(fact_type="test", value="v", confidence=1.5)
        assert f.confidence == 1.0

    def test_confidence_clamped_negative(self):
        f = MemoryFact(fact_type="test", value="v", confidence=-0.3)
        assert f.confidence == 0.0

    def test_confidence_normal_passthrough(self):
        f = MemoryFact(fact_type="test", value="v", confidence=0.7)
        assert f.confidence == 0.7

    def test_is_transient_method_default_threshold(self):
        high = MemoryFact(fact_type="t", value="v", confidence=0.9)
        low = MemoryFact(fact_type="t", value="v", confidence=0.7)
        assert high.is_transient() is False  # 0.9 > 0.85
        assert low.is_transient() is True   # 0.7 <= 0.85

    def test_is_transient_custom_threshold(self):
        f = MemoryFact(fact_type="t", value="v", confidence=0.6)
        assert f.is_transient(threshold=0.5) is False  # 0.6 > 0.5
        assert f.is_transient(threshold=0.6) is True   # 0.6 <= 0.6

    def test_mention_count_default_zero(self):
        f = MemoryFact(fact_type="t", value="v")
        assert f.mention_count == 0

    def test_source_event_id_default_empty(self):
        f = MemoryFact(fact_type="t", value="v")
        assert f.source_event_id == ""

    def test_context_fields_default_empty(self):
        f = MemoryFact(fact_type="t", value="v")
        assert f.context_channel == ""
        assert f.context_topic == ""


# ---- MemoryPolicy tests ----

class TestMemoryPolicy:
    """MemoryPolicy 配置验证"""

    def test_default_values(self):
        p = MemoryPolicy()
        assert p.max_facts_per_user == 50
        assert p.transient_confidence_threshold == 0.85
        assert p.event_dedup_window_minutes == 5
        assert p.max_observed_set_size == 100
        assert p.max_summary_facts_per_type == 5
        assert p.max_summary_total_chars == 2000

    def test_decay_schedule_steeper(self):
        p = MemoryPolicy()
        # 90-day decay should be much more aggressive than old 0.50
        assert p.decay_schedule[90] == 0.30
        # 180-day decay near-zero
        assert p.decay_schedule[180] == 0.05

    def test_memory_policy_on_orchestration(self):
        orch = OrchestrationPolicy(pending_message_threshold=0.0)
        assert isinstance(orch.memory, MemoryPolicy)
        assert orch.memory.max_facts_per_user == 50


# ---- Manager: duplicate dedup + mention_count ----

class TestManagerMentionCount:
    """验证 add_memory_fact 去重提频"""

    def _make_manager(self):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        return m

    def test_duplicate_fact_increments_mention_count(self):
        m = self._make_manager()
        _add(m, "u1", "preference", "likes coffee", "test", 0.7)
        _add(m, "u1", "preference", "likes coffee", "test", 0.8)
        facts = m.entries["u1"].runtime.memory_facts
        assert len(facts) == 1
        assert facts[0].mention_count == 1  # incremented once
        assert facts[0].confidence == 0.8   # updated to higher

    def test_different_facts_not_deduped(self):
        m = self._make_manager()
        _add(m, "u1", "preference", "likes coffee", "test", 0.7)
        _add(m, "u1", "preference", "likes tea", "test", 0.8)
        facts = m.entries["u1"].runtime.memory_facts
        assert len(facts) == 2

    def test_source_event_id_passed(self):
        m = self._make_manager()
        _add(m, "u1", "event", "meeting", "test", 0.7, source_event_id="evt_001")
        fact = m.entries["u1"].runtime.memory_facts[0]
        assert fact.source_event_id == "evt_001"

    def test_memory_category_passed(self):
        m = self._make_manager()
        _add(m, "u1", "preference", "likes coffee", "test", 0.7, memory_category="preference")
        fact = m.entries["u1"].runtime.memory_facts[0]
        assert fact.memory_category == "preference"

    def test_inferred_aliases_are_weak_hints_only(self):
        m = self._make_manager()
        m.apply_ai_runtime_update(
            user_id="u1",
            inferred_aliases=["老王"],
            source="memory_extract",
            confidence=0.8,
        )

        entry = m.entries["u1"]
        assert entry.runtime.inferred_aliases == ["老王"]
        assert entry.profile.aliases == []
        assert m.resolve_user_id(speaker="老王") is None

        restored = UserMemoryManager.from_dict(m.to_dict())
        assert restored.entries["u1"].runtime.inferred_aliases == ["老王"]


# ---- Manager: get_rich_user_summary length control ----

class TestSummaryLengthControl:
    """get_rich_user_summary 按类型限制数量"""

    def _make_manager_with_facts(self, count: int, fact_type: str = "preference"):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        for i in range(count):
            _add(m, "u1", fact_type, f"fact_{i}", "test", 0.5 + i * 0.01)
        return m

    def test_summary_caps_facts_per_type(self):
        m = self._make_manager_with_facts(10)
        summary = m.get_rich_user_summary("u1", max_facts_per_type=3)
        # Summary is a dict with facts_by_type
        fbt = summary.get("facts_by_type", {})
        pref_facts = fbt.get("preference", [])
        assert len(pref_facts) <= 3
        # Highest confidence items should be first
        if pref_facts:
            assert "fact_9" in pref_facts[0]["value"]  # highest confidence = 0.59

    def test_summary_default_limit_5(self):
        m = self._make_manager_with_facts(8)
        summary = m.get_rich_user_summary("u1")
        fbt = summary.get("facts_by_type", {})
        pref_facts = fbt.get("preference", [])
        assert len(pref_facts) <= 5


# ---- Manager: observed set capping ----

class TestObservedSetCapping:
    """apply_event_insights 中 observed_* set 大小限制"""

    def test_cap_set_static_method(self):
        s = {str(i) for i in range(200)}
        UserMemoryManager._cap_set(s, 100)
        assert len(s) == 100

    def test_cap_set_no_change_under_limit(self):
        s = {"1", "2", "3"}
        UserMemoryManager._cap_set(s, 100)
        assert len(s) == 3

    def test_apply_event_insights_caps_observed_sets(self):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        # Create event features with many items
        features: dict[str, object] = {
            "emotion_tags": [f"emo_{i}" for i in range(150)],
            "keywords": [f"kw_{i}" for i in range(150)],
            "role_slots": [f"role_{i}" for i in range(150)],
            "entities": [f"ent_{i}" for i in range(150)],
        }
        m.apply_event_insights("u1", features)
        entry = m.entries["u1"]
        assert len(entry.runtime.observed_emotions) <= 100
        assert len(entry.runtime.observed_keywords) <= 100
        assert len(entry.runtime.observed_roles) <= 100
        assert len(entry.runtime.observed_entities) <= 100

    def test_apply_event_insights_source_event_id(self):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        features: dict[str, object] = {"emotion_tags": ["happy"]}
        m.apply_event_insights("u1", features, source_event_id="evt_42")
        fact = m.entries["u1"].runtime.memory_facts[0]
        assert fact.source_event_id == "evt_42"


# ---- Manager: get_resident_facts / get_transient_facts with threshold ----

class TestResidentTransientThreshold:
    """验证 threshold 参数的传递"""

    def _make_manager(self):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        _add(m, "u1", "t", "low", "test", 0.5)
        _add(m, "u1", "t", "mid", "test", 0.75)
        _add(m, "u1", "t", "high", "test", 0.95)
        return m

    def test_default_threshold_085(self):
        m = self._make_manager()
        resident = m.get_resident_facts("u1")
        transient = m.get_transient_facts("u1")
        assert len(resident) == 1  # only 0.95
        assert len(transient) == 2  # 0.5 and 0.75

    def test_custom_threshold_06(self):
        m = self._make_manager()
        resident = m.get_resident_facts("u1", threshold=0.6)
        transient = m.get_transient_facts("u1", threshold=0.6)
        assert len(resident) == 2  # 0.75 and 0.95
        assert len(transient) == 1  # only 0.5


# ---- Serialization: new fields round-trip ----

class TestSerializationV2:
    """验证新字段的完整序列化/反序列化"""

    def test_round_trip_preserves_new_fields(self):
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        _add(m, "u1", "preference", "likes coffee", "test", 0.7,
             memory_category="preference", source_event_id="evt_01")
        fact = m.entries["u1"].runtime.memory_facts[0]
        fact.mention_count = 3
        fact.context_channel = "qq"
        fact.context_topic = "food"
        fact.observed_time_desc = "昨天下午"

        data = m.to_dict()
        m2 = UserMemoryManager.from_dict(data)
        f2 = m2.entries["u1"].runtime.memory_facts[0]

        assert f2.memory_category == "preference"
        assert f2.source_event_id == "evt_01"
        assert f2.mention_count == 3
        assert f2.context_channel == "qq"
        assert f2.context_topic == "food"
        assert f2.observed_time_desc == "昨天下午"

    def test_backward_compat_old_format(self):
        """Old data without new fields should load with defaults."""
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        data = m.to_dict()
        # Simulate old format: remove new fields from facts payload
        if "u1" in data:
            for f in data["u1"].get("runtime", {}).get("memory_facts", []):
                f.pop("mention_count", None)
                f.pop("source_event_id", None)
                f.pop("context_channel", None)
                f.pop("context_topic", None)
                # Simulate old is_transient field
                f["is_transient"] = True
                f["created_at"] = "2024-01-01T00:00:00"

        m2 = UserMemoryManager.from_dict(data)
        # Should not crash — defaults applied

    def test_cleanup_expired_transient_uses_observed_at(self):
        """cleanup uses observed_at (not removed created_at)."""
        m = UserMemoryManager()
        m.register_user(UserProfile(user_id="u1", name="A"))
        _add(m, "u1", "t", "old", "test", 0.6)
        fact = m.entries["u1"].runtime.memory_facts[0]
        # Set observed_at to 2 hours ago
        fact.observed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        _add(m, "u1", "t", "new", "test", 0.6)

        deleted = m.cleanup_expired_transient_facts("u1", max_age_minutes=30)
        assert deleted == 1
        assert len(m.entries["u1"].runtime.memory_facts) == 1
        assert m.entries["u1"].runtime.memory_facts[0].value == "new"
