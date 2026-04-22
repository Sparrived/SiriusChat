"""User memory management module."""

from __future__ import annotations

# Legacy exports (kept for backward compat; will be removed in cleanup stage)
from sirius_chat.memory.user.manager import (
    MAX_MEMORY_FACTS,
    MAX_OBSERVED_SET_SIZE,
    EVENT_DEDUP_WINDOW_MINUTES,
    UserMemoryManager,
)
from sirius_chat.memory.user.models import (
    MemoryFact,
    UserMemoryEntry,
    UserProfile as _LegacyUserProfile,
    UserRuntimeState,
)
from sirius_chat.memory.user.store import UserMemoryFileStore

# New v2 simplified exports
from sirius_chat.memory.user.simple import (
    UserProfile as SimpleUserProfile,
    UserManager,
)

# Keep old UserProfile available under canonical name for backward compat
UserProfile = _LegacyUserProfile

__all__ = [
    # Legacy
    "MAX_MEMORY_FACTS",
    "MAX_OBSERVED_SET_SIZE",
    "EVENT_DEDUP_WINDOW_MINUTES",
    "UserProfile",
    "UserRuntimeState",
    "MemoryFact",
    "UserMemoryEntry",
    "UserMemoryManager",
    "UserMemoryFileStore",
    # New v2
    "SimpleUserProfile",
    "UserManager",
]
