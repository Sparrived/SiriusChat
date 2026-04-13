"""File-based persistence for AI self-memory."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sirius_chat.memory.self.models import SelfMemoryState
from sirius_chat.memory.self.manager import SelfMemoryManager

logger = logging.getLogger(__name__)


class SelfMemoryFileStore:
    """Persists SelfMemoryState to a single JSON file in work_path."""

    _FILENAME = "self_memory.json"

    def __init__(self, work_path: str | Path) -> None:
        self._dir = Path(work_path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / self._FILENAME

    def load(self) -> SelfMemoryManager:
        """Load self-memory from disk, or return empty manager."""
        if not self._path.exists():
            return SelfMemoryManager()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            manager = SelfMemoryManager.from_dict(payload)
            # Schema write-back: persist any new default fields immediately.
            self.save(manager)
            return manager
        except Exception:
            logger.warning("Failed to load self-memory from %s, starting fresh", self._path)
            return SelfMemoryManager()

    def save(self, manager: SelfMemoryManager) -> None:
        """Persist self-memory to disk."""
        try:
            self._path.write_text(
                json.dumps(manager.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("Failed to save self-memory to %s", self._path)
