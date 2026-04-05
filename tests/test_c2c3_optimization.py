"""
C2/C3优化测试 - RESIDENT/TRANSIENT分离存储和动态压缩
"""

import pytest
from datetime import datetime, timedelta, timezone
from sirius_chat.user_memory import (
    UserMemoryManager,
    UserProfile,
)
from sirius_chat.background_tasks import (
    BackgroundTaskConfig,
    BackgroundTaskManager,
)


class TestC2ResidentTransientSeparation:
    """C2方案: RESIDENT vs TRANSIENT分离存储测试"""

    def test_memory_fact_has_c2_fields(self):
        """测试MemoryFact有C2所需的字段"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Test fact",
            source="test",
            confidence=0.8,
        )
        
        fact = manager.entries["user1"].runtime.memory_facts[0]
        assert hasattr(fact, "is_transient")
        assert hasattr(fact, "created_at")

    def test_fact_with_high_confidence_is_resident(self):
        """测试高置信度facts被标记为RESIDENT"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 高置信度fact
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Important fact",
            source="test",
            confidence=0.95,
        )
        
        fact = manager.entries["user1"].runtime.memory_facts[0]
        assert fact.is_transient == False  # RESIDENT
        assert fact.created_at == ""  # RESIDENT没有created_at

    def test_fact_with_low_confidence_is_transient(self):
        """测试低置信度facts被标记为TRANSIENT"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 低置信度fact
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Uncertain fact",
            source="test",
            confidence=0.7,
        )
        
        fact = manager.entries["user1"].runtime.memory_facts[0]
        assert fact.is_transient == True  # TRANSIENT
        assert fact.created_at != ""  # TRANSIENT有创建时间

    def test_get_resident_facts(self):
        """测试get_resident_facts方法"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加混合的facts
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="High confidence",
            source="test",
            confidence=0.95,
        )
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Low confidence",
            source="test",
            confidence=0.7,
        )
        
        resident_facts = manager.get_resident_facts("user1")
        assert len(resident_facts) == 1
        assert resident_facts[0].value == "High confidence"

    def test_get_transient_facts(self):
        """测试get_transient_facts方法"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加混合的facts
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="High confidence",
            source="test",
            confidence=0.95,
        )
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Low confidence",
            source="test",
            confidence=0.7,
        )
        
        transient_facts = manager.get_transient_facts("user1")
        assert len(transient_facts) == 1
        assert transient_facts[0].value == "Low confidence"

    def test_cleanup_expired_transient_facts(self):
        """测试清理过期的TRANSIENT facts"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加transient fact
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Old transient fact",
            source="test",
            confidence=0.6,
        )
        
        fact = manager.entries["user1"].runtime.memory_facts[0]
        # 修改created_at为过期时间（1小时前）
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        fact.created_at = old_time
        
        # 添加一个新的transient fact
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="New transient fact",
            source="test",
            confidence=0.6,
        )
        
        facts_before = len(manager.entries["user1"].runtime.memory_facts)
        assert facts_before == 2
        
        # 清理，max_age_minutes=30意味着1小时的fact会被删除
        deleted = manager.cleanup_expired_transient_facts("user1", max_age_minutes=30)
        
        assert deleted == 1
        remaining_facts = manager.entries["user1"].runtime.memory_facts
        assert len(remaining_facts) == 1
        assert remaining_facts[0].value == "New transient fact"

    def test_serialization_preserves_c2_fields(self):
        """测试序列化保留C2字段"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        manager.add_memory_fact(
            user_id="user1",
            fact_type="summary",
            value="Test fact",
            source="test",
            confidence=0.7,
        )
        
        # 序列化和反序列化
        data = manager.to_dict()
        manager2 = UserMemoryManager.from_dict(data)
        
        fact = manager2.entries["user1"].runtime.memory_facts[0]
        assert fact.is_transient == True
        assert fact.created_at != ""


class TestC3MemoryCompression:
    """C3方案: 动态压缩memory facts测试"""

    def test_compress_memory_facts_basic(self):
        """测试基本的facts压缩"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加很多相同类型的facts
        for i in range(20):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="user_interest",
                value=f"Interest #{i}",
                source="test",
                confidence=0.5 + (i % 10) * 0.01,
            )
        
        facts_before = len(manager.entries["user1"].runtime.memory_facts)
        assert facts_before == 20
        
        # 压缩：保留top 70%
        deleted = manager.compress_memory_facts("user1")
        
        facts_after = len(manager.entries["user1"].runtime.memory_facts)
        assert deleted > 0
        assert facts_after < facts_before
        # 保留约70%（±2个的浮动）
        assert facts_after >= facts_before * 0.68
        assert facts_after <= facts_before * 0.72

    def test_compress_skips_small_fact_sets(self):
        """测试压缩跳过小的fact集合"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 只添加少量facts
        for i in range(5):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="summary",
                value=f"Fact #{i}",
                source="test",
                confidence=0.8,
            )
        
        deleted = manager.compress_memory_facts("user1")
        
        # 少于10个facts，不应该压缩
        assert deleted == 0
        assert len(manager.entries["user1"].runtime.memory_facts) == 5

    def test_compress_maintains_order(self):
        """测试压缩保保留观察顺序"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加facts
        for i in range(15):
            manager.add_memory_fact(
                user_id="user1",
                fact_type="summary",
                value=f"Fact #{i}",
                source="test",
                confidence=0.5 + (i % 5) * 0.1,
            )
        
        manager.compress_memory_facts("user1")
        
        # 检查是否按observed_at倒序排列
        facts = manager.entries["user1"].runtime.memory_facts
        for i in range(len(facts) - 1):
            assert facts[i].observed_at >= facts[i + 1].observed_at

    def test_compress_preserves_high_confidence(self):
        """测试压缩保留高置信度facts"""
        manager = UserMemoryManager()
        profile = UserProfile(user_id="user1", name="Alice")
        manager.register_user(profile)
        
        # 添加混合confidence的facts
        for i in range(20):
            confidence = 0.9 if i < 3 else 0.5  # 前3个高置信度
            manager.add_memory_fact(
                user_id="user1",
                fact_type="user_interest",
                value=f"Interest #{i}",
                source="test",
                confidence=confidence,
            )
        
        manager.compress_memory_facts("user1")
        
        # 检查是否保留了高置信度facts
        facts = manager.entries["user1"].runtime.memory_facts
        high_confidence_count = sum(1 for f in facts if f.confidence >= 0.9)
        assert high_confidence_count >= 2  # 至少保留大部分高置信度facts


class TestBackgroundTaskManager:
    """后台任务管理器测试"""

    def test_background_task_config_defaults(self):
        """测试后台任务配置默认值"""
        config = BackgroundTaskConfig()
        
        assert config.compression_enabled == True
        assert config.compression_interval_seconds == 3600  # 1小时
        assert config.cleanup_enabled == True
        assert config.cleanup_interval_seconds == 1800  # 30分钟

    @pytest.mark.asyncio
    async def test_background_task_manager_lifecycle(self):
        """测试后台任务管理器的生命周期"""
        config = BackgroundTaskConfig()
        manager = BackgroundTaskManager(config)
        
        assert manager.is_running() == False
        
        await manager.start()
        assert manager.is_running() == True
        
        await manager.stop()
        assert manager.is_running() == False

    @pytest.mark.asyncio
    async def test_background_task_callbacks(self):
        """测试回调函数调用"""
        config = BackgroundTaskConfig()
        manager = BackgroundTaskManager(config)
        
        # 记录回调调用
        compression_called = []
        cleanup_called = []
        
        def compression_callback(user_id: str):
            compression_called.append(user_id)
        
        def cleanup_callback(user_id: str):
            cleanup_called.append(user_id)
        
        manager.set_memory_compressor_callback(compression_callback)
        manager.set_transient_cleanup_callback(cleanup_callback)
        
        # 立即触发（不等待定时）
        await manager.trigger_compression_now("test_user")
        await manager.trigger_cleanup_now("test_user")
        
        assert "test_user" in compression_called
        assert "test_user" in cleanup_called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
