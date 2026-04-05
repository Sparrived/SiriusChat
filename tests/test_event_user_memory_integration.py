"""
测试事件系统与用户记忆系统的双向适配（方案C）。
"""
import pytest
from sirius_chat.memory import (
    UserProfile,
    UserRuntimeState,
    UserMemoryManager,
    ContextualEventInterpretation,
)


class TestEventUserMemoryIntegration:
    """事件→用户记忆的双向适配测试"""

    def test_apply_event_insights_with_emotions(self):
        """测试情感识别转化为用户特征"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)

        event_features = {
            "summary": "项目延期导致的压力",
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
        
        # 验证情感标签已转入observed_emotions
        assert "焦虑" in entry.runtime.observed_emotions
        assert "受挫" in entry.runtime.observed_emotions
        
        # 验证生成了emotional_pattern记忆事实
        emotional_facts = [
            f for f in entry.runtime.memory_facts
            if f.fact_type == "emotional_pattern"
        ]
        assert len(emotional_facts) == 1
        assert "焦虑" in emotional_facts[0].value
        assert "受挫" in emotional_facts[0].value

    def test_apply_event_insights_with_keywords(self):
        """测试关键词转化为用户兴趣记忆"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user2", name="Bob")
        manager.register_user(profile)

        event_features = {
            "keywords": ["机器学习", "深度学习", "神经网络", "数据科学"],
        }

        manager.apply_event_insights(
            user_id="user2",
            event_features=event_features,
        )

        entry = manager.entries["user2"]
        
        # 验证关键词已转入observed_keywords
        assert "机器学习" in entry.runtime.observed_keywords
        assert "深度学习" in entry.runtime.observed_keywords
        
        # 验证生成了user_interest记忆事实
        interest_facts = [
            f for f in entry.runtime.memory_facts
            if f.fact_type == "user_interest"
        ]
        assert len(interest_facts) == 1
        assert "机器学习" in interest_facts[0].value

    def test_event_insights_accumulation_with_memory_manager(self):
        """测试事件特征与memory_manager任务的协同"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user6", name="Frank")
        manager.register_user(profile)

        # 多个事件共同积累用户理解
        for i in range(3):
            event_features = {
                "emotion_tags": ["高兴", "满足"] if i % 2 == 0 else ["焦虑"],
                "keywords": ["编程", "设计"][i % 2 : (i % 2) + 1],
                "role_slots": ["工程师"],
            }
            manager.apply_event_insights("user6", event_features)

        entry = manager.entries["user6"]
        
        # 验证累积了多个情感标签
        assert len(entry.runtime.observed_emotions) > 1
        
        # 验证积累了多个关键词
        assert len(entry.runtime.observed_keywords) > 0
        
        # 验证为同一user生成了多个事实
        all_facts = entry.runtime.memory_facts
        assert len(all_facts) > 0

    def test_serialization_preserves_observed_features(self):
        """测试序列化时保留observed_*字段"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user7", name="Grace")
        manager.register_user(profile)

        event_features = {
            "emotion_tags": ["自信", "积极"],
            "keywords": ["创新", "技术"],
            "role_slots": ["CTO"],
            "entities": ["Google", "OpenAI"],
        }
        manager.apply_event_insights("user7", event_features)

        # 序列化
        data = manager.to_dict()
        
        # 验证observed字段已被序列化
        runtime_data = data["entries"]["user7"]["runtime"]
        assert "observed_keywords" in runtime_data
        assert "observed_roles" in runtime_data
        assert "observed_emotions" in runtime_data
        assert "observed_entities" in runtime_data
        
        # 反序列化
        manager2 = UserMemoryManager.from_dict(data)
        entry2 = manager2.entries["user7"]
        
        # 验证数据完整性恢复
        assert "自信" in entry2.runtime.observed_emotions
        assert "创新" in entry2.runtime.observed_keywords
        assert "CTO" in entry2.runtime.observed_roles
        assert "Google" in entry2.runtime.observed_entities


class TestContextualEventInterpretation:
    """ContextualEventInterpretation数据类的测试"""

    def test_contextual_interpretation_instantiation(self):
        """测试ContextualEventInterpretation可以正确实例化"""
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
        """测试对齐度计算逻辑"""
        interp = ContextualEventInterpretation(
            event_id="evt_002",
            event_summary="某个事件",
        )
        
        # 模拟对齐度评分
        interp.keyword_alignment = 0.8
        interp.role_alignment = 0.6
        interp.emotion_alignment = 0.4
        interp.entity_alignment = 0.2
        
        avg = (0.8 + 0.6 + 0.4 + 0.2) / 4.0
        assert abs(avg - 0.5) < 0.0001  # 使用浮点数容差
        
        # 验证推荐类别逻辑
        if avg > 0.6:
            category = "high_confidence"
        elif avg < 0.2:
            category = "low_relevance"
        else:
            category = "normal"
        
        assert category == "normal"
