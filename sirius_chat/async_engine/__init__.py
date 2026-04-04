"""Async orchestration engine for Sirius Chat.

This package provides the core async orchestration engine for managing
multi-turn conversations with AI agents, user memory management, and
coordinated task execution.

For backward compatibility, AsyncRolePlayEngine is re-exported at the
package level. Internal modules provide specialized functionality:

- core: Main AsyncRolePlayEngine class
- prompts: System prompt building
- utils: Utility functions for token estimation, JSON extraction, etc.
"""

from __future__ import annotations

from sirius_chat.async_engine.core import AsyncRolePlayEngine

__all__ = [
    "AsyncRolePlayEngine",
]
