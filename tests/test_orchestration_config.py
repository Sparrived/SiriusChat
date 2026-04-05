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
from sirius_chat import AsyncRolePlayEngine
from sirius_chat.exceptions import OrchestrationConfigError
from sirius_chat.async_engine.orchestration_config import (
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
                unified_model="gpt-4",
            ),
        )

    def test_orchestration_enabled_without_any_configured_tasks_no_error(self):
        """当多模型协同使用统一模型时，不需要配置特定的任务模型。"""
        config = self.create_base_config()
        # 使用统一模型配置（方案1）
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常
        engine.validate_orchestration_config(config)

    def test_orchestration_unified_model_strategy(self):
        """测试多模型协同的方案1（统一模型）。"""
        config = self.create_base_config()
        # unified_model 已在 create_base_config 中设置
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常
        engine.validate_orchestration_config(config)

    def test_orchestration_enabled_without_required_models_raises_error(self):
        """当多模型协同使用 task_models 且启用了多个任务但仅配置了部分模型时抛出异常。"""
        config = self.create_base_config()
        # 切换到按任务配置方案
        config.orchestration.unified_model = ""
        # 配置 memory_extract 但不配置其他任务
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        # 所有任务都默认启用（task_enabled 未改动）
        # 因为没有其他任务的模型配置，应该抛出异常
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该抛出异常，因为 multimodal_parse 和 event_extract 已启用但没有模型
        with pytest.raises(OrchestrationConfigError):
            engine.validate_orchestration_config(config)

    def test_orchestration_enabled_with_disabled_tasks_no_error(self):
        """当多模型协同使用 task_models 且禁用了没有模型的任务时不抛出异常。"""
        config = self.create_base_config()
        # 切换到按任务配置方案
        config.orchestration.unified_model = ""
        # 配置 memory_extract 但不配置其他任务
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        # 禁用没有模型的任务
        config.orchestration.task_enabled = {
            "memory_extract": True,
            "multimodal_parse": False,
            "event_extract": False,
        }
        # 仅在已启用的任务中启用预算
        config.orchestration.task_budgets = {
            "memory_extract": 1000,
        }
        
        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)
        
        # 应该不抛出异常，因为只有已配置的任务被启用
        # 其他任务已被禁用，所以不需要配置模型
        engine.validate_orchestration_config(config)

    def test_orchestration_task_models_all_enabled_tasks_must_have_models(self):
        """当多模型协同使用 task_models 且启用了多个任务时，所有启用的任务都必须有模型。"""
        config = self.create_base_config()
        # 切换到按任务配置方案
        config.orchestration.unified_model = ""
        # 配置 memory_extract 但不配置其他任务
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        # 启用所有任务
        config.orchestration.task_budgets = {
            "memory_extract": 1000,
            "multimodal_parse": 1000,
            "event_extract": 1000,
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
        """当所有启用的任务都配置了模型时，不抛出异常。"""
        config = self.create_base_config()
        # 切换到按任务配置方案
        config.orchestration.unified_model = ""
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
            "multimodal_parse": "gpt-4-mini",
            "event_extract": "gpt-4-mini",
        }
        # 启用所有任务
        config.orchestration.task_budgets = {
            "memory_extract": 1000,
            "multimodal_parse": 1000,
            "event_extract": 1000,
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
        """测试 run_live_session 在部分配置且启用了所有任务时进行验证。"""
        config = self.create_base_config()
        # 切换到按任务配置模式
        config.orchestration.unified_model = ""
        # 只配置一个任务
        config.orchestration.task_models = {"memory_extract": "gpt-4-mini"}
        # 启用所有任务（这会导致错误，因为并非所有任务都有模型）
        config.orchestration.task_budgets = {
            "memory_extract": 1000,
            "multimodal_parse": 1000,
            "event_extract": 1000,
        }
        
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
        # 切换到按任务配置模式
        config.orchestration.unified_model = ""
        # 配置一个任务但不配置其他任务
        config.orchestration.task_models = {"event_extract": "gpt-4-mini"}
        # 启用所有任务（这会导致错误）
        config.orchestration.task_budgets = {
            "event_extract": 1000,
            "memory_extract": 1000,
            "multimodal_parse": 1000,
        }

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
