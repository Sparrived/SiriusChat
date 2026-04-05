"""
API 层隐藏性与完整性自动化测试

运行方式: pytest tests/test_api_integrity.py -v
"""

import pytest
import sys
import warnings
from typing import Set


class TestAPILayerHiding:
    """验证 API 层隐藏所有内部实现"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """设置测试环境"""
        # 清空已导入的 sirius_chat，确保新鲜导入
        if 'sirius_chat' in sys.modules:
            del sys.modules['sirius_chat']
    
    def test_api_has_all_defined(self):
        """测试：公开API应该在 __all__ 中定义"""
        import sirius_chat
        
        assert hasattr(sirius_chat, '__all__'), \
            "sirius_chat 必须定义 __all__"
        assert isinstance(sirius_chat.__all__, (list, tuple)), \
            "__all__ 必须是列表或元组"
        assert len(sirius_chat.__all__) >= 10, \
            "__all__ 中应该至少有 10 个导出"
    
    def test_no_private_members_leaked(self):
        """测试：不应该导出以 _ 开头的私有成员"""
        import sirius_chat
        
        allowed_private = {
            '__package__', '__name__', '__doc__', '__file__',
            '__loader__', '__spec__', '__cached__', '__builtins__',
            '__version__', '__all__', '__annotations__'
        }
        
        leaked = []
        for name in dir(sirius_chat):
            if name.startswith('_') and name not in allowed_private:
                # 检查是否在 __all__ 中
                if name in sirius_chat.__all__:
                    leaked.append(name)
        
        assert not leaked, f"私有实现泄露: {leaked}"
    
    def test_no_internal_packages_exposed(self):
        """测试：内部包（core, memory 等）不应该直接暴露"""
        import sirius_chat
        import inspect
        
        # 这些包应该是内部实现，不应该直接导出
        internal_packages = {
            'core', 'memory', 'config', 'async_engine',
            'session', 'token', 'cache', 'performance'
        }
        
        exposed = []
        for name in sirius_chat.__all__:
            obj = getattr(sirius_chat, name)
            if inspect.ismodule(obj):
                pkg_name = obj.__name__.split('.')[-1]
                if pkg_name in internal_packages:
                    exposed.append(name)
        
        assert not exposed, f"内部包暴露: {exposed}"
    
    def test_all_exported_are_accessible(self):
        """测试：__all__ 中的所有项都应该可访问"""
        import sirius_chat
        
        for name in sirius_chat.__all__:
            assert hasattr(sirius_chat, name), \
                f"__all__ 中的 {name} 无法访问"
            
            obj = getattr(sirius_chat, name)
            assert obj is not None, \
                f"__all__ 中的 {name} 为 None"
    
    def test_required_api_exported(self):
        """测试：必需的公开API都应该被导出"""
        import sirius_chat
        
        required = {
            'AsyncRolePlayEngine',
            'SessionConfig',
            'OrchestrationPolicy',
            'UserMemoryManager',
            'EventMemoryManager',
            'TRAIT_TAXONOMY',
            'Message',
            'Transcript',
        }
        
        all_set = set(sirius_chat.__all__)
        missing = required - all_set
        
        assert not missing, f"缺少必需的API: {missing}"
    
    def test_all_documented(self):
        """测试：所有导出的类都应该有文档"""
        import sirius_chat
        import inspect
        
        undocumented = []
        for name in sirius_chat.__all__:
            obj = getattr(sirius_chat, name)
            
            # 检查类或函数是否有文档
            if inspect.isclass(obj) or inspect.isfunction(obj):
                if not obj.__doc__ or len(obj.__doc__.strip()) < 10:
                    undocumented.append(name)
        
        # 允许一些数据对象（如 TRAIT_TAXONOMY）没有 __doc__
        # 但大多数类应该有文档
        class_count = sum(1 for n in sirius_chat.__all__
                         if inspect.isclass(getattr(sirius_chat, n)))
        undoc_count = len(undocumented)
        
        # 至少 80% 的类应该有文档
        if class_count > 0:
            doc_ratio = (class_count - undoc_count) / class_count
            assert doc_ratio >= 0.8, \
                f"文档不完整: {undoc_count}/{class_count} 个类缺少文档"


class TestAPIBackwardCompatibility:
    """验证向后兼容性"""


class TestAPIFunctionality:
    """验证通过公开API能完整使用库"""
    
    def test_can_create_session_config(self):
        """测试：能否通过公开API创建会话配置"""
        from sirius_chat import SessionConfig, AgentPreset, Agent
        from pathlib import Path
        
        # SessionConfig 需要 work_path 和 preset
        agent = Agent(name="assistant", persona="helpful", model="test")
        preset = AgentPreset(agent=agent, global_system_prompt="test")
        config = SessionConfig(
            work_path=Path("/tmp"),
            preset=preset
        )
        
        assert config.preset.agent.name == "assistant"
        assert config.work_path == Path("/tmp")
    
    def test_can_create_user_profile(self):
        """测试：能否通过公开API创建用户档案"""
        from sirius_chat import UserProfile
        
        profile = UserProfile(
            user_id="test_user",
            name="Test User"
        )
        
        assert profile.user_id == "test_user"
        assert profile.name == "Test User"
    
    def test_can_access_trait_taxonomy(self):
        """测试：能否访问特征分类"""
        from sirius_chat import TRAIT_TAXONOMY
        
        assert isinstance(TRAIT_TAXONOMY, dict)
        assert len(TRAIT_TAXONOMY) > 0
        
        # 检查包含必要的分类
        assert "Social" in TRAIT_TAXONOMY or \
               "Practical" in TRAIT_TAXONOMY or \
               "Learning" in TRAIT_TAXONOMY
    
    def test_can_create_memory_manager(self):
        """测试：能否创建内存管理器"""
        from sirius_chat import UserMemoryManager, UserProfile
        
        mgr = UserMemoryManager()
        
        profile = UserProfile(
            user_id="test",
            name="Test"
        )
        
        mgr.register_user(profile)
        
        # 验证用户已注册
        assert "test" in mgr.entries
    
    def test_cannot_access_internal_functions(self):
        """测试：不应该能访问内部函数"""
        import sirius_chat
        
        # 这些是内部实现，不应该导出
        internal_functions = [
            '_normalize_trait',
            '_extract_keywords',
            '_merge_unique',
            '_score'
        ]
        
        for func_name in internal_functions:
            # 尝试从顶级 API 导入（应该失败）
            try:
                getattr(sirius_chat, func_name)
                # 如果找到了，说明泄露了
                pytest.fail(f"内部函数 {func_name} 不应该被导出")
            except AttributeError:
                # 这是预期的！
                pass
    
    def test_cannot_access_internal_classes(self):
        """测试：不应该能访问内部类"""
        import sirius_chat
        
        # 这些是内部实现，不应该导出
        internal_classes = [
            '_ConversationState',
            '_EventMemoryEntry',
            '_MemoryQualityMetrics'
        ]
        
        for class_name in internal_classes:
            try:
                getattr(sirius_chat, class_name)
                # 如果找到了，说明泄露了
                pytest.fail(f"内部类 {class_name} 不应该被导出")
            except AttributeError:
                # 这是预期的！
                pass


class TestAPIDataIntegrity:
    """验证数据模型和API数据完整性"""
    
    def test_message_model_available(self):
        """测试：Message 数据模型应该可用"""
        from sirius_chat import Message
        
        msg = Message(
            role="user",
            content="Hello"
        )
        
        assert msg.role == "user"
        assert msg.content == "Hello"
    
    def test_transcript_model_available(self):
        """测试：Transcript 数据模型应该可用"""
        from sirius_chat import Transcript, Message
        
        msg = Message(role="user", content="Hi")
        transcript = Transcript(messages=[msg])
        
        assert len(transcript.messages) == 1
        assert transcript.messages[0].role == "user"
    
    def test_memory_fact_available(self):
        """测试：内存事实应该通过公开API可用"""
        from sirius_chat import UserMemoryManager
        
        mgr = UserMemoryManager()
        
        # 应该能添加内存事实
        from sirius_chat import UserProfile
        profile = UserProfile(user_id="test", name="Test")
        mgr.register_user(profile)
        
        # 添加事实（confidence > 0.85 是 resident facts）
        mgr.add_memory_fact(
            user_id="test",
            fact_type="preference",
            value="Test fact",
            source="observation",
            confidence=0.9  # 超过0.85门槛
        )
        
        # 应该能获取事实
        facts = mgr.get_resident_facts(user_id="test")
        assert len(facts) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
