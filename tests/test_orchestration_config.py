"""多模型协同配置检查的单元测试。"""

import pytest
import asyncio
from pathlib import Path

from sirius_chat.models import (
    SessionConfig,
    Agent,
    AgentPreset,
    Message,
    OrchestrationPolicy,
)
from sirius_chat.async_engine.core import AsyncRolePlayEngine
from sirius_chat.exceptions import OrchestrationConfigError
from sirius_chat.orchestration_config import (
    configure_orchestration_models,
    configure_orchestration_budgets,
    configure_orchestration_temperatures,
    configure_full_orchestration,
)
from sirius_chat.providers.mock import MockProvider


class TestOrchestrationConfigValidation:
    """测试多模型协同配置验证。"""

    def create_base_config(self) -> SessionConfig:
        """创建基础配置。"""
        agent = Agent(
            name="TestAgent",
            persona="A helpful assistant.",
            model="gpt-4",
        )
        preset = AgentPreset(
            agent=agent,
            global_system_prompt="You are a helpful assistant.",
        )
        return SessionConfig(
            work_path=Path("./test_data"),
            preset=preset,
            orchestration=OrchestrationPolicy(
                enabled=True,
                task_models={},  # 初始为空
            ),
        )

    def test_orchestration_enabled_without_any_configured_tasks_no_error(self):
        """当多模型协同启用但没有配置任何任务时，不抛出异常。"""
        config = self.create_base_config()
        # orchestration.enabled=True 但 task_models 为空
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常，因为没有任何任务被配置
        engine.validate_orchestration_config(config)

    def test_orchestration_disabled_no_validation(self):
        """当多模型协同禁用时，不进行验证。"""
        config = self.create_base_config()
        config.orchestration.enabled = False
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常
        engine.validate_orchestration_config(config)

    def test_orchestration_enabled_without_required_models_raises_error(self):
        """当多模型协同启用且任何任务已配置但缺少其他任务时抛出异常。"""
        config = self.create_base_config()
        # 配置 memory_extract 但不配置其他任务
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        with pytest.raises(OrchestrationConfigError) as exc_info:
            engine.validate_orchestration_config(config)
        
        error = exc_info.value
        assert len(error.missing_models) == 2  # 另外两个必需任务
        assert "multimodal_parse" in error.missing_models
        assert "event_extract" in error.missing_models

    def test_orchestration_enabled_with_all_models_no_error(self):
        """当所有必需模型都配置时，不抛出异常。"""
        config = self.create_base_config()
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
            "multimodal_parse": "gpt-4-mini",
            "event_extract": "gpt-4-mini",
        }
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常
        engine.validate_orchestration_config(config)

    def test_configure_orchestration_models(self):
        """测试配置任务模型函数。"""
        config = self.create_base_config()
        
        updated_config = configure_orchestration_models(
            config,
            memory_extract="gpt-4-mini",
            multimodal_parse="gpt-4-mini",
            event_extract="gpt-4-mini",
        )
        
        assert updated_config.orchestration.task_models["memory_extract"] == "gpt-4-mini"
        assert updated_config.orchestration.task_models["multimodal_parse"] == "gpt-4-mini"
        assert updated_config.orchestration.task_models["event_extract"] == "gpt-4-mini"
        # 原配置应该不变
        assert config.orchestration.task_models == {}

    def test_configure_orchestration_budgets(self):
        """测试配置任务预算函数。"""
        config = self.create_base_config()
        
        updated_config = configure_orchestration_budgets(
            config,
            memory_extract=1000,
            event_extract=500,
        )
        
        assert updated_config.orchestration.task_budgets["memory_extract"] == 1000
        assert updated_config.orchestration.task_budgets["event_extract"] == 500

    def test_configure_orchestration_temperatures(self):
        """测试配置任务温度函数。"""
        config = self.create_base_config()
        
        updated_config = configure_orchestration_temperatures(
            config,
            memory_extract=0.1,
            event_extract=0.3,
        )
        
        assert updated_config.orchestration.task_temperatures["memory_extract"] == 0.1
        assert updated_config.orchestration.task_temperatures["event_extract"] == 0.3

    def test_configure_full_orchestration(self):
        """测试一次性配置所有参数函数。"""
        config = self.create_base_config()
        
        updated_config = configure_full_orchestration(
            config,
            task_models={
                "memory_extract": "gpt-4-mini",
                "multimodal_parse": "gpt-4-mini",
                "event_extract": "gpt-4-mini",
            },
            task_budgets={
                "memory_extract": 1000,
                "event_extract": 500,
            },
            task_temperatures={
                "memory_extract": 0.1,
            },
            memory_manager_model="gpt-4",
        )
        
        # 验证所有配置都已应用
        assert updated_config.orchestration.task_models["memory_extract"] == "gpt-4-mini"
        assert updated_config.orchestration.task_budgets["memory_extract"] == 1000
        assert updated_config.orchestration.task_temperatures["memory_extract"] == 0.1
        assert updated_config.orchestration.memory_manager_model == "gpt-4"

    @pytest.mark.asyncio
    async def test_run_live_session_validates_config_when_partial_config(self):
        """测试 run_live_session 在部分配置时进行验证。"""
        config = self.create_base_config()
        # 只配置一个任务
        config.orchestration.task_models = {"memory_extract": "gpt-4-mini"}
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        human_turns = [
            Message(role="user", speaker="User1", content="Hello"),
        ]
        
        with pytest.raises(OrchestrationConfigError):
            await engine.run_live_session(
                config=config,
                human_turns=human_turns,
            )

    @pytest.mark.asyncio
    async def test_run_live_session_with_valid_config(self):
        """测试 run_live_session 在配置有效时正常执行。"""
        config = self.create_base_config()
        # 配置所有必需模型
        config.orchestration.task_models = {
            "memory_extract": "mock",
            "multimodal_parse": "mock",
            "event_extract": "mock",
        }
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        human_turns = [
            Message(role="user", speaker="User1", content="Hello"),
        ]
        
        # 应该不抛出异常
        transcript = await engine.run_live_session(
            config=config,
            human_turns=human_turns,
        )
        
        assert transcript is not None
        assert len(transcript.messages) > 0

    def test_error_message_clarity(self):
        """测试错误消息的清晰性。"""
        config = self.create_base_config()
        # 配置一个任务但不配置其他任务
        config.orchestration.task_models = {"event_extract": "gpt-4-mini"}

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        try:
            engine.validate_orchestration_config(config)
            pytest.fail("Expected OrchestrationConfigError")
        except OrchestrationConfigError as e:
            error_message = str(e)
            # 验证错误消息包含有用的信息
            assert "多模型协同已启用" in error_message or "缺少" in error_message or "缺失" in error_message
            assert "configure_orchestration_models" in error_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
