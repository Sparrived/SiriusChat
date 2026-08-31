from __future__ import annotations

import asyncio

import pytest

from sirius_pulse.plugins.api import BackgroundTaskSpec
from sirius_pulse.plugins.base import PluginBase
from sirius_pulse.plugins.executor import PluginExecutor
from sirius_pulse.plugins.models import (
    CommandAST,
    PluginDefinition,
    PluginPermissionDef,
    PluginRenderDef,
    PluginResponse,
)
from sirius_pulse.plugins.registry import PluginRegistry
from sirius_pulse.plugins.scheduler import PluginScheduler, ScheduledTask


class _CommandPlugin(PluginBase):
    _plugin_name = "command_demo"

    def execute(self, cmd: CommandAST) -> PluginResponse:
        return PluginResponse.ok(text="executed")


class _BackgroundPlugin(PluginBase):
    _plugin_name = "background_demo"
    _plugin_display_name = "Background demo"
    _plugin_permissions = {"hidden_from_intent": True}

    def __init__(self) -> None:
        super().__init__()
        self.ticks = 0

    async def _tick(self) -> None:
        self.ticks += 1

    def create_background_tasks(self) -> list[BackgroundTaskSpec]:
        return [BackgroundTaskSpec("tick", 0.001, self._tick)]


@pytest.mark.asyncio
async def test_plugin_executor_when_plugin_is_disabled_then_it_is_not_instantiated(tmp_path):
    registry = PluginRegistry()
    definition = PluginDefinition(
        name="background_demo",
        display_name="Background demo",
        permissions=PluginPermissionDef(),
        render=PluginRenderDef(),
        source_path=None,
    )
    definition._plugin_class = _BackgroundPlugin
    registry.register(definition)

    from sirius_pulse.plugins.config import PluginConfigManager

    config = PluginConfigManager(tmp_path)
    config.set_enabled("background_demo", False)
    executor = PluginExecutor(registry, persona_data_path=tmp_path, config_manager=config)

    assert await executor.instantiate_all() == 0
    assert registry.get_instance("background_demo") is None


@pytest.mark.asyncio
async def test_plugin_executor_when_disabled_after_load_then_skips_execution(tmp_path):
    registry = PluginRegistry()
    definition = PluginDefinition(
        name="command_demo",
        display_name="Command demo",
        permissions=PluginPermissionDef(),
        render=PluginRenderDef(),
        source_path=None,
    )
    definition._plugin_class = _CommandPlugin
    registry.register(definition)

    from sirius_pulse.plugins.config import PluginConfigManager

    config = PluginConfigManager(tmp_path)
    executor = PluginExecutor(registry, persona_data_path=tmp_path, config_manager=config)
    assert await executor.instantiate_all() == 1

    config.set_enabled("command_demo", False)
    result = await executor.execute("command_demo", CommandAST(command="demo", raw_text="/demo"))

    assert len(result) == 1
    assert result[0].success is False
    assert result[0].render_mode == "silent"


@pytest.mark.asyncio
async def test_plugin_executor_starts_and_cancels_declared_background_tasks(tmp_path):
    registry = PluginRegistry()
    definition = PluginDefinition(
        name="background_demo",
        display_name="Background demo",
        description="background task",
        version="1.0",
        permissions=PluginPermissionDef(hidden_from_intent=True),
        render=PluginRenderDef(),
        source_path=None,
    )
    definition._plugin_class = _BackgroundPlugin
    registry.register(definition)
    executor = PluginExecutor(registry, persona_data_path=tmp_path)

    assert await executor.instantiate_all() == 1
    instance = registry.get_instance("background_demo")
    assert isinstance(instance, _BackgroundPlugin)

    running = True
    assert await executor.start_background_tasks(running_check=lambda: running) == 1
    assert await executor.start_background_tasks(running_check=lambda: running) == 0
    await asyncio.sleep(0.03)
    assert instance.ticks > 0

    running = False
    await executor.unload("background_demo")
    assert executor._background_tasks == {}
    assert registry.get_instance("background_demo") is None


@pytest.mark.asyncio
async def test_plugin_scheduler_runs_other_tasks_while_one_task_is_waiting():
    first_started = asyncio.Event()
    second_finished = asyncio.Event()

    async def slow_callback() -> None:
        first_started.set()
        await asyncio.sleep(0.05)

    async def fast_callback() -> None:
        second_finished.set()

    scheduler = PluginScheduler(check_interval=0.001)
    scheduler.add_task(ScheduledTask("slow", "one", interval_seconds=0.001, callback=slow_callback))
    scheduler.add_task(ScheduledTask("fast", "two", interval_seconds=0.001, callback=fast_callback))
    await scheduler.start()
    await asyncio.wait_for(first_started.wait(), timeout=0.2)
    await asyncio.wait_for(second_finished.wait(), timeout=0.2)
    await scheduler.stop()

    assert not scheduler._callback_tasks
