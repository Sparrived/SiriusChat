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

# Import from new location, but re-export for backward compatibility
from sirius_chat.core import AsyncRolePlayEngine
from sirius_chat.async_engine.orchestration import (
    SUPPORTED_MULTIMODAL_TYPES,
    TASK_EVENT_EXTRACT,
    TASK_MEMORY_EXTRACT,
    TASK_MEMORY_MANAGER,
    TASK_MULTIMODAL_PARSE,
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
    "TASK_MULTIMODAL_PARSE",
    "TASK_EVENT_EXTRACT",
    "TASK_MEMORY_MANAGER",
    "SUPPORTED_MULTIMODAL_TYPES",
]
