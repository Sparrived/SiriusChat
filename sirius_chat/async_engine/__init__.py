"""Async orchestration engine for Sirius Chat.

This package provides the core async orchestration engine for managing
multi-turn conversations with AI agents, user memory management, and
coordinated task execution.

For backward compatibility, AsyncRolePlayEngine is re-exported at the
package level. Internal modules provide specialized functionality:

- core: Main AsyncRolePlayEngine class
- prompts: System prompt building
- utils: Utility functions for token estimation, JSON extraction, etc.
- orchestration: Task configuration and orchestration helpers
"""

from __future__ import annotations

# Legacy engine archived; minimal stub preserved for transitional imports.
class AsyncRolePlayEngine:
    def __init__(self, *args, **kwargs) -> None:
        self._provider = args[0] if args else kwargs.get("provider")

    def _has_multimodal_inputs(self, transcript) -> bool:
        return False

    def _get_model_for_chat(self, config, transcript) -> str:
        return config.preset.agent.model if hasattr(config, "preset") else "mock-model"

    async def _generate_assistant_message(self, config, transcript, message):
        from sirius_chat.models import Message
        return Message(role="assistant", content="mock")

    def _build_system_prompt(self, config, transcript, environment_context: str = "") -> str:
        return config.preset.global_system_prompt if hasattr(config, "preset") else ""

    def validate_orchestration_config(self, config) -> None:
        pass

    async def run_live_session(self, *, config, transcript=None):
        from sirius_chat.models import Transcript
        return transcript or Transcript()

    async def run_live_message(self, *, config, turn, transcript=None, environment_context: str = "", **kwargs):
        from sirius_chat.models import Transcript
        return transcript or Transcript()

    def subscribe(self, callback, **kwargs) -> None:
        pass

    def set_shared_skill_runtime(self, *args, **kwargs) -> None:
        pass

from sirius_chat.async_engine.orchestration import (
    SUPPORTED_MULTIMODAL_TYPES,
    TASK_EVENT_EXTRACT,
    TASK_INTENT_ANALYSIS,
    TASK_MEMORY_EXTRACT,
    TASK_MEMORY_MANAGER,
    TaskConfig,
    get_system_prompt_for_task,
    get_task_config,
)

__all__ = [
    "AsyncRolePlayEngine",
    # Orchestration exports
    "TaskConfig",
    "get_task_config",
    "get_system_prompt_for_task",
    "TASK_MEMORY_EXTRACT",
    "TASK_EVENT_EXTRACT",
    "TASK_INTENT_ANALYSIS",
    "TASK_MEMORY_MANAGER",
    "SUPPORTED_MULTIMODAL_TYPES",
]
