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
            └── {group_id}.json
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

    @staticmethod
    def _load_entries(path: Path) -> list[dict[str, Any]]:
        """Load entries from file, supporting both legacy jsonl and new JSON-array format."""
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                return []
            # New format: JSON object with "entries" array
            if text.startswith("{"):
                data = json.loads(text)
                return list(data.get("entries", []))
            # Legacy format: jsonl (one JSON object per line)
            entries = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return entries
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _save_entries(path: Path, entries: list[dict[str, Any]]) -> None:
        """Save entries as a formatted JSON array (human-readable)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def add_entry(self, entry: EventMemoryEntry) -> None:
        """Append an entry to the group's episodic memory."""
        import dataclasses
        path = self._entry_path(entry.group_id or "default")
        entries = self._load_entries(path)
        entries.append(dataclasses.asdict(entry))
        self._save_entries(path, entries)

    def add_event(
        self,
        *,
        group_id: str,
        user_id: str,
        content: str,
        summary: str = "",
        emotion_valence: float = 0.0,
        importance: float = 0.5,
    ) -> None:
        """Convenience method: create and append a simple event entry.

        Args:
            summary: Human-readable event summary. If empty, a brief description
                is auto-generated from user_id and content.
        """
        import uuid
        from datetime import datetime, timezone
        if not summary:
            text = content.strip()
            if text.startswith("[图片:"):
                summary = f"{user_id} 分享了一张图片"
            elif text.startswith("[SKILL_CALL:"):
                summary = f"{user_id} 调用了技能"
            elif len(text) <= 30:
                summary = f"{user_id} 说: {text}"
            else:
                summary = f"{user_id} 提到: {text[:60]}"
        entry = EventMemoryEntry(
            event_id=str(uuid.uuid4()),
            user_id=user_id,
            group_id=group_id,
            summary=summary,
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
        raw_entries = self._load_entries(path)

        results = []
        for data in raw_entries:
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
        raw_entries = self._load_entries(path)

        results = []
        for data in raw_entries:
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
        raw_entries = self._load_entries(path)
        if not raw_entries:
            return 0

        kept_entries = []
        archived_entries = []
        archive_path = self._base_dir / f"{self._safe_name(group_id)}_archive.json"

        for data in raw_entries:
            activation = self._activation.calculate_activation(
                importance=float(data.get("confidence", 0.5)),
                created_at=str(data.get("created_at", "")),
                access_count=int(data.get("access_count", 0)),
                memory_category=str(data.get("category", "custom")),
            )
            data["activation"] = round(activation, 6)

            if self._activation.should_archive(activation):
                archived_entries.append(data)
            else:
                kept_entries.append(data)

        # Rewrite kept entries in formatted JSON array
        self._save_entries(path, kept_entries)

        # Append archived entries
        if archived_entries:
            existing_archive = self._load_entries(archive_path)
            existing_archive.extend(archived_entries)
            self._save_entries(archive_path, existing_archive)
            logger.info(
                "%s 群的往事有点沉了，我把 %d 条淡去的回忆轻轻收进了档案室。",
                group_id,
                len(archived_entries),
            )

        return len(archived_entries)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _entry_path(self, group_id: str) -> Path:
        return self._base_dir / f"{self._safe_name(group_id)}.json"

    @staticmethod
    def _safe_name(name: str) -> str:
        import re
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", name.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "default"
