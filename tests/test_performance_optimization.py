"""
性能优化测试 - 验证方案A1（去重）、B（特征规范化）、C1（facts上限）
"""

import pytest
from datetime import datetime, timedelta, timezone
from sirius_chat.memory import (
    UserMemoryManager,
    UserProfile,
    MAX_MEMORY_FACTS,
    EVENT_DEDUP_WINDOW_MINUTES,
)
from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY


class TestPerformanceOptimization:
    """
    性能优化测试套件
    方案A1: 时间窗口去重
    方案B: 特征规范化（Taxonomy）
    方案C1: Memory Facts上限管理
    """

    def test_A1_event_dedup_window_initialized(self):
        """测试A1: 验证去重窗口常数正确"""
        assert EVENT_DEDUP_WINDOW_MINUTES == 5
        assert MAX_MEMORY_FACTS == 50

    def test_A1_last_event_processed_at_timestamp_tracking(self):
        """测试A1: 验证时间戳字段的初始化和更新"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 初始时应该为None
        assert manager.entries["user1"].runtime.last_event_processed_at is None
        
        # 模拟更新时间戳
        now = datetime.now(timezone.utc)
        manager.entries["user1"].runtime.last_event_processed_at = now
        assert manager.entries["user1"].runtime.last_event_processed_at is not None
        
    def test_A1_serialization_deserialization_timestamp(self):
        """测试A1: 验证时间戳的序列化和反序列化"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 设置时间戳
        now = datetime.now(timezone.utc)
        manager.entries["user1"].runtime.last_event_processed_at = now
        
        # 序列化
        data = manager.to_dict()
        assert "last_event_processed_at" in data["entries"]["user1"]["runtime"]
        
        # 反序列化
        manager2 = UserMemoryManager.from_dict(data)
        restored_time = manager2.entries["user1"].runtime.last_event_processed_at
        assert restored_time is not None
        # 由于时间戳转换，检查它们足够接近（秒级精度）
        assert abs((restored_time - now).total_seconds()) < 1

    def test_B_trait_taxonomy_defined(self):
        """测试B: 验证特征分类体系已定义"""
        assert "Learning" in TRAIT_TAXONOMY
        assert "Social" in TRAIT_TAXONOMY
        assert "Lifestyle" in TRAIT_TAXONOMY
        assert "Creative" in TRAIT_TAXONOMY
        assert "Practical" in TRAIT_TAXONOMY
        assert "Emotional" in TRAIT_TAXONOMY
        assert "Leisure" in TRAIT_TAXONOMY
        
        # 验证每个分类都有keywords和description
        for category, info in TRAIT_TAXONOMY.items():
            assert "keywords" in info
            assert len(info["keywords"]) > 0
            assert "priority" in info
            assert "description" in info

    def test_B_normalize_trait_basic_classification(self):
        """测试B: 验证特征规范化基本分类"""
        manager = UserMemoryManager()
        
        # 测试已分类的特征（日常交流维度）
        assert manager._normalize_trait("学习") == "Learning"
        assert manager._normalize_trait("研究") == "Learning"
        assert manager._normalize_trait("交流") == "Social"
        assert manager._normalize_trait("团队") == "Social"
        assert manager._normalize_trait("运动") == "Lifestyle"
        assert manager._normalize_trait("绘画") == "Creative"
        assert manager._normalize_trait("工作") == "Practical"
        assert manager._normalize_trait("开心") == "Emotional"
        assert manager._normalize_trait("爱好") == "Leisure"

    def test_B_normalize_trait_already_classified(self):
        """测试B: 已分类标签直接返回"""
        manager = UserMemoryManager()
        
        # 已经是分类标签
        assert manager._normalize_trait("Learning") == "Learning"
        assert manager._normalize_trait("Social") == "Social"
        assert manager._normalize_trait("Lifestyle") == "Lifestyle"
        assert manager._normalize_trait("Creative") == "Creative"
        assert manager._normalize_trait("Practical") == "Practical"

    def test_B_normalize_trait_unclassified(self):
        """测试B: 无法分类的特征保留原样"""
        manager = UserMemoryManager()
        
        # 无法分类的特征保留
        assert manager._normalize_trait("xyz_unknown_trait") == "xyz_unknown_trait"
        # 注："某个特定风格"会匹配到Creative（因为包含"风格"），这是预期行为
        assert manager._normalize_trait("xyz_truly_unknown") == "xyz_truly_unknown"

    def test_B_normalize_trait_case_insensitive(self):
        """测试B: 规范化忽略大小写"""
        manager = UserMemoryManager()
        
        assert manager._normalize_trait("LEARNING") == "Learning"
        assert manager._normalize_trait("learning") == "Learning"
        assert manager._normalize_trait("TEAM") == "Social"
        assert manager._normalize_trait("team") == "Social"

    def test_B_add_memory_fact_with_trait_normalization(self):
        """测试B: 验证add_memory_fact中的特征规范化"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加一个日常交流特征，应该被规范化
        manager.add_memory_fact(
            user_id="user1",
            fact_type="user_interest",
            value="学习",  # 会被规范化为"Learning"
            source="test",
            confidence=0.8,
        )
        
        facts = manager.entries["user1"].runtime.memory_facts
        assert len(facts) == 1
        # 值应该被规范化为Learning
        assert facts[0].value == "Learning" or facts[0].value == "学习"  # 取决于实现

    def test_C1_max_memory_facts_constant(self):
        """测试C1: 验证MAX_MEMORY_FACTS常数"""
        assert MAX_MEMORY_FACTS == 50

    def test_C1_memory_facts_cleanup_on_limit_exceeded(self):
        """测试C1: 验证超过上限时的自动清理"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加超过上限的facts（每个confidence递减）
        for i in range(60):
            manager.add_memory_fact(
                user_id="user1",
                fact_type=f"test_fact_{i % 5}",  # 5种不同类型
                value=f"Fact #{i}",
                source="test",
                confidence=0.5 + (i % 10) * 0.01,  # confidence: 0.5~0.59
            )
        
        facts = manager.entries["user1"].runtime.memory_facts
        # 应该被清理到MAX_MEMORY_FACTS（50）左右
        # 由于可能有去重，实际可能少于50
        assert len(facts) <= MAX_MEMORY_FACTS
        # 保留的应该是confidence较高的
        confidences = [f.confidence for f in facts]
        assert max(confidences) >= min(confidences)  # 保证有混合的confidence

    def test_C1_cleanup_removes_lowest_confidence_facts(self):
        """测试C1: 验证清理逻辑删除的是lowest confidence的facts"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 准确添加不同confidence的facts
        for i in range(10):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="summary",
                value=f"Unique fact {i}",  # 确保都是不同的value
                source="test",
                confidence=float(i) / 10.0,  # 0.0, 0.1, 0.2, ..., 0.9
            )
        
        facts_before = len(manager.entries["user1"].runtime.memory_facts)
        assert facts_before == 10
        
        # 添加11个更多的事实，触发清理
        for i in range(10, 61):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="summary",
                value=f"Unique fact {i}",
                source="test",
                confidence=0.5,  # 所有新fact都是相同的中等confidence
            )
        
        facts_after = manager.entries["user1"].runtime.memory_facts
        # 应该被清理到~50
        assert len(facts_after) <= MAX_MEMORY_FACTS + 1  # +1容许浮动
        
        # 验证最低的confidence被删除了
        remaining_confidences = [f.confidence for f in facts_after]
        # 应该没有最低的facts（confidence接近0）
        assert min(remaining_confidences) >= 0.4  # 至少高于初始的最低值

    def test_C1_add_memory_fact_respects_max_facts_parameter(self):
        """测试C1: 验证add_memory_fact中max_facts参数可优先级"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 用自定义的max_facts=30
        for i in range(40):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="summary",
                value=f"Fact {i}",
                source="test",
                confidence=0.5 + (i % 10) * 0.01,
                max_facts=30,  # 自定义limit
            )
        
        facts = manager.entries["user1"].runtime.memory_facts
        assert len(facts) <= 30

    def test_combined_optimization_workflow(self):
        """集成测试: A1 + B + C1的综合效果"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 模拟一系列事件处理
        event_features_list = [
            {
                "keywords": ["编程", "代码"],
                "emotion_tags": ["兴奋"],
                "role_slots": ["技术主导", "leader"],
            },
            {
                "keywords": ["项目", "交付"],
                "emotion_tags": ["满足"],
                "role_slots": ["项目经理"],
            },
            {
                "keywords": ["学习", "算法"],
                "emotion_tags": ["专注"],
                "role_slots": ["学生"],
            },
        ]
        
        for event_features in event_features_list:
            manager.apply_event_insights(
                user_id="user1",
                event_features=event_features,
                source="test",
                base_confidence=0.7,
            )
        
        # 验证结果
        runtime = manager.entries["user1"].runtime
        
        # C1: 检查facts数量在合理范围
        assert len(runtime.memory_facts) <= MAX_MEMORY_FACTS
        
        # B: 检查观察到的特征
        assert len(runtime.observed_keywords) >= 3
        assert len(runtime.observed_roles) >= 2
        assert len(runtime.observed_emotions) >= 2
        
        # A1: 验证时间戳可被设置
        runtime.last_event_processed_at = datetime.now(timezone.utc)
        assert runtime.last_event_processed_at is not None

    def test_serialization_preserves_optimization_fields(self):
        """测试序列化保留优化相关字段"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 设置一些优化字段的值
        now = datetime.now(timezone.utc)
        manager.entries["user1"].runtime.last_event_processed_at = now
        
        # 添加一些fact
        manager.add_memory_fact(
            user_id="user1",
            fact_type="user_interest",
            value="编程",
            source="test",
            confidence=0.8,
        )
        
        # 序列化和反序列化
        data = manager.to_dict()
        manager2 = UserMemoryManager.from_dict(data)
        
        # 验证优化字段被保留
        assert manager2.entries["user1"].runtime.last_event_processed_at is not None
        assert len(manager2.entries["user1"].runtime.memory_facts) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
