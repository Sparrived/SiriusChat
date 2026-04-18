"""Episodic memory manager: structured event storage per group."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sirius_chat.memory.activation_engine import ActivationEngine
from sirius_chat.memory.event.models import EventMemoryEntry
from sirius_chat.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)


class EpisodicMemoryManager:
    """Manages episodic memory entries per group.

    Storage layout:
        {work_path}/episodic/
            └── {group_id}.jsonl
    """

    def __init__(
        self,
        work_path: Path | WorkspaceLayout,
        activation_engine: ActivationEngine | None = None,
    ) -> None:
        layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._base_dir = layout.work_path / "episodic"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._activation = activation_engine or ActivationEngine()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_entry(self, entry: EventMemoryEntry) -> None:
        """Append an entry to the group's episodic memory."""
        import dataclasses
        path = self._entry_path(entry.group_id or "default")
        line = json.dumps(dataclasses.asdict(entry), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def add_event(
        self,
        *,
        group_id: str,
        user_id: str,
        content: str,
        emotion_valence: float = 0.0,
        importance: float = 0.5,
    ) -> None:
        """Convenience method: create and append a simple event entry."""
        import uuid
        from datetime import datetime, timezone
        entry = EventMemoryEntry(
            event_id=str(uuid.uuid4()),
            user_id=user_id,
            group_id=group_id,
            summary=content,
            category="custom",
            confidence=min(1.0, max(0.0, importance)),
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            activation=min(1.0, max(0.0, importance)),
        )
        self.add_entry(entry)

    def get_entries(
        self,
        group_id: str,
        *,
        user_id: str | None = None,
        category: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[EventMemoryEntry]:
        """Query entries with optional filters."""
        path = self._entry_path(group_id)
        if not path.exists():
            return []

        results = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if user_id and data.get("user_id") != user_id:
                    continue
                if category and data.get("category") != category:
                    continue
                if data.get("confidence", 0.0) < min_confidence:
                    continue

                results.append(EventMemoryEntry(**data))
                if len(results) >= limit:
                    break

        return results

    def search_by_keyword(
        self,
        group_id: str,
        keyword: str,
        limit: int = 20,
    ) -> list[EventMemoryEntry]:
        """Simple keyword search in entry summaries."""
        keyword_lower = keyword.lower()
        path = self._entry_path(group_id)
        if not path.exists():
            return []

        results = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                summary = str(data.get("summary", "")).lower()
                if keyword_lower in summary:
                    results.append(EventMemoryEntry(**data))
                    if len(results) >= limit:
                        break

        return results

    def recalculate_activations(self, group_id: str) -> int:
        """Recalculate activation for all entries in a group and rewrite file.

        Returns number of entries archived (activation below threshold).
        """
        path = self._entry_path(group_id)
        if not path.exists():
            return 0

        kept_lines = []
        archived_lines = []
        archive_path = self._base_dir / f"{self._safe_name(group_id)}_archive.jsonl"

        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)
                    continue

                activation = self._activation.calculate_activation(
                    importance=float(data.get("confidence", 0.5)),
                    created_at=str(data.get("created_at", "")),
                    access_count=int(data.get("access_count", 0)),
                    memory_category=str(data.get("category", "custom")),
                )
                data["activation"] = round(activation, 6)

                if self._activation.should_archive(activation):
                    archived_lines.append(json.dumps(data, ensure_ascii=False))
                else:
                    kept_lines.append(json.dumps(data, ensure_ascii=False))

        # Rewrite kept entries
        with path.open("w", encoding="utf-8") as f:
            for line in kept_lines:
                f.write(line + "\n")

        # Append archived entries
        if archived_lines:
            with archive_path.open("a", encoding="utf-8") as f:
                for line in archived_lines:
                    f.write(line + "\n")
            logger.info(
                "%s 群的往事有点沉了，我把 %d 条淡去的回忆轻轻收进了档案室。",
                group_id,
                len(archived_lines),
            )

        return len(archived_lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _entry_path(self, group_id: str) -> Path:
        return self._base_dir / f"{self._safe_name(group_id)}.jsonl"

    @staticmethod
    def _safe_name(name: str) -> str:
        import re
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", name.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "default"
