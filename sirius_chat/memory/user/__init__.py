"""Simplified user system: core identity only."""

from __future__ import annotations

from sirius_chat.memory.user.models import UserProfile
from sirius_chat.memory.user.manager import UserManager

__all__ = [
    "UserProfile",
    "UserManager",
]
