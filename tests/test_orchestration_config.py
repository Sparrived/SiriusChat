"""多模型协同配置检查的单元测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip("Legacy AsyncRolePlayEngine tests unavailable after v0.28 refactor")

from sirius_chat import AsyncRolePlayEngine
from sirius_chat.async_engine.orchestration_config import (
    configure_full_orchestration,
    configure_orchestration_models,
    configure_orchestration_temperatures,
)
from sirius_chat.config import Agent, AgentPreset, OrchestrationPolicy, SessionConfig
from sirius_chat.exceptions import OrchestrationConfigError
from sirius_chat.models import Message
from sirius_chat.providers.base import GenerationRequest
from sirius_chat.providers.mock import MockProvider


def _create_base_config(work_path: Path) -> SessionConfig:
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
        work_path=work_path,
        preset=preset,
        orchestration=OrchestrationPolicy(
            unified_model="gpt-4",
            pending_message_threshold=0,
        ),
    )


class TestOrchestrationConfigValidation:
    def test_orchestration_unified_model_strategy(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "unified")

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        engine.validate_orchestration_config(config)

    def test_orchestration_enabled_without_required_models_raises_error(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "missing-model")
        config.orchestration.unified_model = ""
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        config.orchestration.task_enabled = {
            "memory_extract": True,
            "event_extract": True,
            "intent_analysis": False,
            "memory_manager": False,
        }

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        with pytest.raises(OrchestrationConfigError) as exc_info:
            engine.validate_orchestration_config(config)

        assert "event_extract" in exc_info.value.missing_models

    def test_orchestration_enabled_with_disabled_tasks_no_error(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "disabled-task")
        config.orchestration.unified_model = ""
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
        }
        config.orchestration.task_enabled = {
            "memory_extract": True,
            "event_extract": False,
            "intent_analysis": False,
            "memory_manager": False,
        }

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        engine.validate_orchestration_config(config)

    def test_orchestration_enabled_with_all_models_no_error(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "all-models")
        config.orchestration.unified_model = ""
        config.orchestration.task_models = {
            "memory_extract": "gpt-4-mini",
            "event_extract": "gpt-4-mini",
        }
        config.orchestration.task_enabled = {
            "memory_extract": True,
            "event_extract": True,
            "intent_analysis": False,
            "memory_manager": False,
        }

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        engine.validate_orchestration_config(config)

    def test_configure_orchestration_models(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "configure-models")

        updated_config = configure_orchestration_models(
            config,
            memory_extract="gpt-4-mini",
            event_extract="gpt-4-mini",
        )

        assert updated_config.orchestration.task_models["memory_extract"] == "gpt-4-mini"
        assert updated_config.orchestration.task_models["event_extract"] == "gpt-4-mini"
        assert config.orchestration.task_models == {}

    def test_configure_orchestration_temperatures(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "configure-temps")

        updated_config = configure_orchestration_temperatures(
            config,
            memory_extract=0.1,
            event_extract=0.3,
        )

        assert updated_config.orchestration.task_temperatures["memory_extract"] == 0.1
        assert updated_config.orchestration.task_temperatures["event_extract"] == 0.3

    def test_configure_full_orchestration_uses_task_models_for_memory_manager(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "configure-full")

        updated_config = configure_full_orchestration(
            config,
            task_models={
                "memory_extract": "gpt-4-mini",
                "event_extract": "gpt-4-mini",
                "memory_manager": "gpt-4",
            },
            task_temperatures={
                "memory_extract": 0.1,
                "memory_manager": 0.3,
            },
            task_retries={
                "memory_extract": 1,
            },
            pending_message_threshold=0,
        )

        assert updated_config.orchestration.task_models["memory_extract"] == "gpt-4-mini"
        assert updated_config.orchestration.task_models["memory_manager"] == "gpt-4"
        assert updated_config.orchestration.task_temperatures["memory_manager"] == 0.3
        assert updated_config.orchestration.task_retries["memory_extract"] == 1
        assert updated_config.orchestration.pending_message_threshold == 0

    def test_run_live_session_validates_config_when_required_task_model_missing(self, tmp_path: Path) -> None:
        async def _run() -> None:
            config = _create_base_config(tmp_path / "run-missing")
            config.orchestration.unified_model = ""
            config.orchestration.task_models = {"memory_extract": "gpt-4-mini"}
            config.orchestration.task_enabled = {
                "memory_extract": True,
                "event_extract": True,
                "intent_analysis": False,
                "memory_manager": False,
            }

            provider = MockProvider()
            engine = AsyncRolePlayEngine(provider=provider)

            with pytest.raises(OrchestrationConfigError):
                await engine.run_live_session(config=config)

        asyncio.run(_run())

    def test_run_live_session_with_valid_task_models(self, tmp_path: Path) -> None:
        class RoutingProvider:
            def __init__(self) -> None:
                self.requests: list[tuple[str, str]] = []

            def generate(self, request: GenerationRequest) -> str:
                self.requests.append((str(request.purpose), request.model))
                if request.purpose == "memory_extract":
                    return json.dumps(
                        {
                            "inferred_persona": "测试用户",
                            "inferred_aliases": [],
                            "inferred_traits": [],
                            "preference_tags": [],
                            "summary_note": "",
                        },
                        ensure_ascii=False,
                    )
                if request.purpose == "event_extract":
                    return "[]"
                return "主回复"

        async def _run() -> None:
            config = _create_base_config(tmp_path / "run-valid")
            config.orchestration.unified_model = ""
            config.orchestration.task_models = {
                "memory_extract": "memory-model",
                "event_extract": "event-model",
            }
            config.orchestration.task_enabled = {
                "memory_extract": True,
                "event_extract": True,
                "intent_analysis": False,
                "memory_manager": False,
            }
            config.orchestration.event_extract_batch_size = 1

            provider = RoutingProvider()
            engine = AsyncRolePlayEngine(provider=provider)

            transcript = await engine.run_live_session(config=config)
            transcript = await engine.run_live_message(
                config=config,
                transcript=transcript,
                turn=Message(role="user", speaker="User1", content="Hello"),
                finalize_and_persist=False,
            )

            assert transcript is not None
            assert ("memory_extract", "memory-model") in provider.requests
            assert provider.requests[-1] == ("chat_main", "gpt-4")

        asyncio.run(_run())

    def test_error_message_clarity(self, tmp_path: Path) -> None:
        config = _create_base_config(tmp_path / "error-message")
        config.orchestration.unified_model = ""
        config.orchestration.task_models = {"event_extract": "gpt-4-mini"}
        config.orchestration.task_enabled = {
            "memory_extract": True,
            "event_extract": True,
            "intent_analysis": False,
            "memory_manager": False,
        }

        provider = MockProvider()
        engine = AsyncRolePlayEngine(provider=provider)

        with pytest.raises(OrchestrationConfigError) as exc_info:
            engine.validate_orchestration_config(config)

        error_message = str(exc_info.value)
        assert "缺少" in error_message or "缺失" in error_message
        assert "memory_extract" in error_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])