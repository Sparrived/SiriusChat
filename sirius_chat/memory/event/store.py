"""Event memory file store implementation"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sirius_chat.memory.event.manager import EventMemoryManager

logger = logging.getLogger(__name__)


class EventMemoryFileStore:
    """File-based storage for event memory."""
    
    def __init__(self, work_path: Path, filename: str = "events.json") -> None:
        self._dir = Path(work_path) / "events"
        self._path = self._dir / filename

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> EventMemoryManager:
        """Load event memory from file."""
        if not self._path.exists():
            return EventMemoryManager()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return EventMemoryManager()
        if not isinstance(payload, dict):
            return EventMemoryManager()
        return EventMemoryManager.from_dict(payload)

    def save(self, manager: EventMemoryManager) -> None:
        """Save event memory to file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(manager.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
