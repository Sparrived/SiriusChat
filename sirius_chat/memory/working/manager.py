"""Working memory manager: per-group sliding window with importance-weighted truncation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.working.models import WorkingMemoryEntry

logger = logging.getLogger(__name__)

DEFAULT_MAX_SIZE = 20
PROMOTE_THRESHOLD = 0.3
PROTECTED_IMPORTANCE = 0.7


class WorkingMemoryManager:
    """Manages per-group working memory windows.

    Each group has its own in-memory sliding window. When the window exceeds
    max_size, entries are sorted by (importance, timestamp) and low-importance
    old entries are removed. Removed entries with importance >= PROMOTE_THRESHOLD
    are flagged for promotion to episodic memory.
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE) -> None:
        self.max_size = max_size
        # group_id -> list of WorkingMemoryEntry
        self._windows: dict[str, list[WorkingMemoryEntry]] = {}
        # Preloaded memories for pending responses
        self._preload_cache: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add_entry(
        self,
        group_id: str,
        user_id: str,
        role: str,
        content: str,
        *,
        importance: float = 0.5,
        emotion_state: dict[str, Any] | None = None,
        channel: str = "",
        channel_user_id: str = "",
        timestamp: str | None = None,
    ) -> WorkingMemoryEntry:
        """Add an entry to a group's working memory and manage the window."""
        gid = group_id or "default"
        entry = WorkingMemoryEntry(
            entry_id=f"wme_{uuid.uuid4().hex[:12]}",
            group_id=gid,
            user_id=user_id,
            role=role,
            content=content,
            timestamp=timestamp or self._now_iso(),
            importance=importance,
            protected=_is_protected(content, importance),
            emotion_state=dict(emotion_state or {}),
            channel=channel,
            channel_user_id=channel_user_id,
        )

        if gid not in self._windows:
            self._windows[gid] = []

        self._windows[gid].append(entry)
        self._manage_window(gid)
        return entry

    def get_window(self, group_id: str) -> list[WorkingMemoryEntry]:
        """Get the working memory window for a group."""
        return list(self._windows.get(group_id or "default", []))

    def get_recent_entries(
        self,
        group_id: str,
        n: int = 10,
    ) -> list[WorkingMemoryEntry]:
        """Get the most recent n entries for a group."""
        window = self._windows.get(group_id or "default", [])
        return list(window[-n:])

    def get_entries_by_user(
        self,
        group_id: str,
        user_id: str,
    ) -> list[WorkingMemoryEntry]:
        """Get all entries from a specific user in a group."""
        return [
            e for e in self._windows.get(group_id or "default", [])
            if e.user_id == user_id
        ]

    def clear_group(self, group_id: str) -> None:
        """Clear working memory for a specific group."""
        self._windows.pop(group_id or "default", None)
        self._preload_cache.pop(group_id or "default", None)

    # ------------------------------------------------------------------
    # Preload cache (for contextual pre-loading)
    # ------------------------------------------------------------------

    def set_preload(self, group_id: str, memories: list[dict[str, Any]]) -> None:
        """Cache pre-loaded memories for a group."""
        self._preload_cache[group_id or "default"] = list(memories)

    def get_preload(self, group_id: str) -> list[dict[str, Any]]:
        """Retrieve pre-loaded memories for a group and clear the cache."""
        return list(self._preload_cache.pop(group_id or "default", []))

    # ------------------------------------------------------------------
    # Window management
    # ------------------------------------------------------------------

    def _manage_window(self, group_id: str) -> list[WorkingMemoryEntry]:
        """Truncate window if it exceeds max_size.

        Returns list of removed entries that should be promoted to episodic memory.
        """
        window = self._windows.get(group_id, [])
        if len(window) <= self.max_size:
            return []

        # Sort by (importance desc, timestamp desc), but protect protected entries
        sorted_entries = sorted(
            window,
            key=lambda e: (1 if e.protected else 0, e.importance, e.timestamp),
            reverse=True,
        )

        keep_ids = {e.entry_id for e in sorted_entries[: self.max_size]}
        kept: list[WorkingMemoryEntry] = []
        promoted: list[WorkingMemoryEntry] = []
        discarded: list[WorkingMemoryEntry] = []

        for e in window:
            if e.entry_id in keep_ids:
                kept.append(e)
            elif e.importance >= PROMOTE_THRESHOLD:
                promoted.append(e)
            else:
                discarded.append(e)

        self._windows[group_id] = kept

        if promoted:
            logger.debug(
                "Group %s: promoted %d entries to episodic memory",
                group_id,
                len(promoted),
            )
        if discarded:
            logger.debug(
                "Group %s: discarded %d low-importance entries",
                group_id,
                len(discarded),
            )

        return promoted

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            gid: [e.to_dict() for e in entries]
            for gid, entries in self._windows.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkingMemoryManager":
        mgr = cls()
        for gid, entries in data.items():
            mgr._windows[gid] = [
                WorkingMemoryEntry.from_dict(e) for e in entries if isinstance(e, dict)
            ]
        return mgr

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_PROTECTED_KEYWORDS = {
    "喜欢", "讨厌", "最爱", "偏好", "习惯", "约定", "答应", "承诺",
    "deadline", "截止日期", "生日", "纪念日", "地址", "电话", "联系方式",
    "崩溃", "绝望", "想死", "自杀", "自残", "救命", "紧急",
}


def _is_protected(content: str, importance: float) -> bool:
    """Heuristic: mark entries containing key personal info or crisis signals as protected."""
    if importance >= PROTECTED_IMPORTANCE:
        return True
    text = content.lower()
    for kw in _PROTECTED_KEYWORDS:
        if kw in text:
            return True
    return False
