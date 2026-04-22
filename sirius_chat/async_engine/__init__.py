"""Async orchestration engine compatibility layer for Sirius Chat.

This package provides prompts, task configuration, and utility helpers.
The legacy AsyncRolePlayEngine has been removed in v1.0.0.
"""

from __future__ import annotations

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
