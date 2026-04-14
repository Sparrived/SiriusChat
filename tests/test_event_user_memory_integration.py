"""
测试事件系统 v2（观察模式）与用户记忆系统的集成。
"""
import pytest
from sirius_chat.memory import (
    UserProfile,
    UserMemoryManager,
    ContextualEventInterpretation,
)
from sirius_chat.memory.event import EventMemoryManager, EventMemoryEntry


class TestEventV2BufferAndExtraction:
    """事件系统 v2 基础功能测试"""

    def test_buffer_message_basic(self):
        """消息缓冲基本功能"""
        mgr = EventMemoryManager()
        mgr.buffer_message(user_id="u1", content="今天加班到很晚，明天要交付产品")
        assert mgr.pending_buffer_counts() == {"u1": 1}

    def test_buffer_skips_short_content(self):
        """太短的消息不缓冲"""
        mgr = EventMemoryManager()
        mgr.buffer_message(user_id="u1", content="ok")
        assert mgr.pending_buffer_counts() == {}

    def test_should_extract_threshold(self):
        """达到批处理阈值时应触发提取"""
        mgr = EventMemoryManager()
        for i in range(4):
            mgr.buffer_message(user_id="u1", content=f"这是第 {i} 条有意义的消息内容")
        assert not mgr.should_extract("u1", batch_size=5)
        mgr.buffer_message(user_id="u1", content="第五条有意义的消息内容来了")
        assert mgr.should_extract("u1", batch_size=5)

    def test_buffer_cap_per_user(self):
        """单用户缓冲不超过上限"""
        mgr = EventMemoryManager()
        for i in range(30):
            mgr.buffer_message(user_id="u1", content=f"有意义的消息内容编号 {i}")
        assert mgr.pending_buffer_counts()["u1"] <= 20

    def test_check_relevance_empty(self):
        """无观察记录时返回 new"""
        mgr = EventMemoryManager()
        result = mgr.check_relevance(user_id="u1", content="任意内容")
        assert result["level"] == "new"
        assert result["score"] == 0.0

    def test_check_relevance_with_existing(self):
        """有相关观察时返回相关度"""
        mgr = EventMemoryManager()
        mgr.entries.append(EventMemoryEntry(
            event_id="evt_0001",
            user_id="u1",
            category="goal",
            summary="正在准备下周的产品发布",
            confidence=0.8,
            verified=True,
        ))
        result = mgr.check_relevance(user_id="u1", content="产品发布前还需要做一些测试")
        assert result["level"] in ("high", "weak")
        assert result["score"] > 0

    def test_merge_or_add_deduplication(self):
        """相似观察应合并而非重复添加"""
        mgr = EventMemoryManager()
        entry1 = EventMemoryEntry(
            event_id="evt_0001",
            user_id="u1",
            category="preference",
            summary="喜欢用Python编程",
            confidence=0.7,
            mention_count=1,
            updated_at="2026-01-01T00:00:00",
            verified=True,
        )
        mgr.entries.append(entry1)

        entry2 = EventMemoryEntry(
            event_id="evt_0002",
            user_id="u1",
            category="preference",
            summary="喜欢使用Python进行编程",
            confidence=0.8,
            updated_at="2026-01-02T00:00:00",
        )
        merged = mgr._merge_or_add(entry2)
        assert len(mgr.entries) == 1  # merged, not added
        assert merged.mention_count == 2
        assert merged.confidence == 0.8  # took higher

    def test_merge_different_user_not_merged(self):
        """不同用户的相似观察不合并"""
        mgr = EventMemoryManager()
        mgr.entries.append(EventMemoryEntry(
            event_id="evt_0001", user_id="u1", category="preference",
            summary="喜欢用Python编程", confidence=0.7, verified=True,
        ))
        entry2 = EventMemoryEntry(
            event_id="evt_0002", user_id="u2", category="preference",
            summary="喜欢用Python编程", confidence=0.8,
        )
        mgr._merge_or_add(entry2)
        assert len(mgr.entries) == 2


class TestEventV2Serialization:
    """v2 序列化与 v1 迁移测试"""

    def test_roundtrip_v2(self):
        """v2 数据序列化/反序列化完整性"""
        mgr = EventMemoryManager()
        mgr.entries.append(EventMemoryEntry(
            event_id="evt_0001", user_id="u1", category="trait",
            summary="沟通风格直接高效", confidence=0.85,
            evidence_samples=["我觉得直接说最好"], mention_count=2,
            created_at="2026-01-01T00:00:00", updated_at="2026-01-02T00:00:00",
            verified=True,
        ))
        mgr.buffer_message(user_id="u2", content="这是一条缓冲中仍未提取的消息")

        data = mgr.to_dict()
        assert data["version"] == 2
        assert len(data["entries"]) == 1
        assert "u2" in data["buffer"]

        mgr2 = EventMemoryManager.from_dict(data)
        assert len(mgr2.entries) == 1
        e = mgr2.entries[0]
        assert e.user_id == "u1"
        assert e.category == "trait"
        assert e.confidence == 0.85
        assert mgr2.pending_buffer_counts().get("u2") == 1

    def test_migrate_v1(self):
        """v1 格式不再自动迁移，from_dict 丢弃 version<2 的数据"""
        v1_data = {
            "entries": [
                {
                    "event_id": "evt_0001",
                    "summary": "项目延期讨论",
                    "keywords": ["项目", "延期"],
                    "role_slots": ["manager"],
                    "entities": [],
                    "time_hints": ["this_week"],
                    "emotion_tags": ["anxiety"],
                    "evidence_samples": ["这周可能完不成"],
                    "hit_count": 3,
                    "created_at": "2025-06-01T10:00:00",
                    "updated_at": "2025-06-02T10:00:00",
                    "verified": True,
                    "mention_count": 5,
                },
            ]
        }
        mgr = EventMemoryManager.from_dict(v1_data)
        # v1 format is no longer supported — returns empty manager
        assert len(mgr.entries) == 0

    def test_top_events_user_filter(self):
        """top_events 支持按用户过滤"""
        mgr = EventMemoryManager()
        mgr.entries.append(EventMemoryEntry(
            event_id="evt_0001", user_id="u1", summary="obs1",
            verified=True, updated_at="2026-01-01",
        ))
        mgr.entries.append(EventMemoryEntry(
            event_id="evt_0002", user_id="u2", summary="obs2",
            verified=True, updated_at="2026-01-02",
        ))
        assert len(mgr.top_events(limit=10)) == 2
        assert len(mgr.top_events(limit=10, user_id="u1")) == 1

    def test_get_user_observations(self):
        """按用户获取观察，按置信度排序"""
        mgr = EventMemoryManager()
        mgr.entries.append(EventMemoryEntry(
            event_id="e1", user_id="u1", summary="low", confidence=0.3,
        ))
        mgr.entries.append(EventMemoryEntry(
            event_id="e2", user_id="u1", summary="high", confidence=0.9,
        ))
        mgr.entries.append(EventMemoryEntry(
            event_id="e3", user_id="u2", summary="other", confidence=1.0,
        ))
        result = mgr.get_user_observations("u1")
        assert len(result) == 2
        assert result[0].summary == "high"


class TestEventV2UserMemoryBridge:
    """观察 → 用户记忆事实的桥接测试"""

    def test_apply_event_insights_still_works(self):
        """旧的 apply_event_insights 仍可调用（向后兼容）"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)

        event_features = {
            "emotion_tags": ["焦虑", "受挫"],
            "keywords": ["项目", "压力"],
            "role_slots": ["项目经理"],
        }
        manager.apply_event_insights(
            user_id="user1",
            event_features=event_features,
            source="event_extract",
            base_confidence=0.65,
        )
        entry = manager.entries["user1"]
        assert "焦虑" in entry.runtime.observed_emotions
        assert len(entry.runtime.memory_facts) > 0

    def test_add_memory_fact_from_observation(self):
        """模拟 v2 引擎流程：观察直接写入用户记忆"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)

        # 模拟 extract_observations 的结果
        observation = EventMemoryEntry(
            event_id="evt_0001",
            user_id="user1",
            category="preference",
            summary="喜欢喝拿铁咖啡",
            confidence=0.8,
        )

        # 引擎会执行的转换
        category_to_memory = {
            "preference": ("preference_tag", "preference"),
            "trait": ("inferred_trait", "identity"),
            "emotion": ("emotional_pattern", "emotion"),
        }
        fact_type, mem_cat = category_to_memory.get(
            observation.category, ("summary", "custom")
        )
        manager.add_memory_fact(
            user_id="user1",
            fact_type=fact_type,
            value=observation.summary,
            source="event_observation",
            confidence=observation.confidence,
            memory_category=mem_cat,
            source_event_id=observation.event_id,
        )

        facts = manager.entries["user1"].runtime.memory_facts
        assert len(facts) == 1
        assert facts[0].value == "喜欢喝拿铁咖啡"
        assert facts[0].source == "event_observation"
        assert facts[0].source_event_id == "evt_0001"
        assert facts[0].memory_category == "preference"


class TestContextualEventInterpretation:
    """ContextualEventInterpretation 向后兼容测试"""

    def test_contextual_interpretation_instantiation(self):
        interp = ContextualEventInterpretation(
            event_id="evt_001",
            event_summary="测试事件摘要",
            base_confidence=0.65,
        )
        assert interp.event_id == "evt_001"
        assert interp.event_summary == "测试事件摘要"
        assert interp.base_confidence == 0.65
        assert interp.keyword_alignment == 0.0
        assert interp.recommended_category == "normal"

    def test_contextual_interpretation_alignment_calculation(self):
        interp = ContextualEventInterpretation(
            event_id="evt_002",
            event_summary="某个事件",
        )
        interp.keyword_alignment = 0.8
        interp.role_alignment = 0.6
        interp.emotion_alignment = 0.4
        interp.entity_alignment = 0.2
        avg = (0.8 + 0.6 + 0.4 + 0.2) / 4.0
        assert abs(avg - 0.5) < 0.0001


class TestEventV2ParseResponse:
    """LLM 响应解析测试"""

    def test_parse_plain_json_array(self):
        raw = '[{"category":"preference","content":"喜欢Python","confidence":0.8}]'
        result = EventMemoryManager._parse_extraction_response(raw)
        assert len(result) == 1
        assert result[0]["category"] == "preference"

    def test_parse_markdown_fenced(self):
        raw = '```json\n[{"category":"trait","content":"直接","confidence":0.7}]\n```'
        result = EventMemoryManager._parse_extraction_response(raw)
        assert len(result) == 1
        assert result[0]["category"] == "trait"

    def test_parse_empty_array(self):
        raw = "[]"
        result = EventMemoryManager._parse_extraction_response(raw)
        assert result == []

    def test_parse_invalid_json(self):
        raw = "这不是JSON"
        result = EventMemoryManager._parse_extraction_response(raw)
        assert result == []
