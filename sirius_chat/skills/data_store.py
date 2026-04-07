"""Persistent key-value data store for skills.

Each skill gets an isolated JSON-backed store under {work_path}/skill_data/{skill_name}.json.
This allows skills to persist data across invocations.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SkillDataStore:
    """JSON-backed persistent key-value store for a single skill.

    Thread-safety: not guaranteed. Expected to be used within a single
    async task at a time (the engine serializes skill calls).
    """

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    self._data = loaded
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("SKILL数据存储加载失败 (%s): %s", self._path, exc)

    def save(self) -> None:
        """Persist current data to disk (only if modified)."""
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value by key. Call save() to persist."""
        self._data[key] = value
        self._dirty = True

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        if key in self._data:
            del self._data[key]
            self._dirty = True
            return True
        return False

    def keys(self) -> list[str]:
        """Return all stored keys."""
        return list(self._data.keys())

    def all(self) -> dict[str, Any]:
        """Return a shallow copy of all stored data."""
        return dict(self._data)

    @property
    def is_dirty(self) -> bool:
        return self._dirty
