"""User memory file store with group-isolated layout (v0.28+).

Layout:
    {work_path}/user_memory/global/                  # Cross-group profiles (future)
    {work_path}/user_memory/groups/{group_id}/
        ├── {user_id}.json
        └── group_state.json
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sirius_chat.memory.user.manager import UserMemoryManager
from sirius_chat.memory.user.models import MemoryFact, UserMemoryEntry
from sirius_chat.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)


class UserMemoryFileStore:
    """File-based storage for group-isolated user memory."""

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._base_dir = layout.user_memory_dir()

    @property
    def directory(self) -> Path:
        return self._base_dir

    @staticmethod
    def _safe_filename(user_id: str) -> str:
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", user_id.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "user"

    @staticmethod
    def _entry_to_payload(entry: UserMemoryEntry) -> dict[str, Any]:
        return {
            "profile": entry.profile.to_dict(),
            "runtime": {
                "inferred_persona": entry.runtime.inferred_persona,
                "inferred_traits": entry.runtime.inferred_traits,
                "preference_tags": entry.runtime.preference_tags,
                "recent_messages": entry.runtime.recent_messages,
                "summary_notes": entry.runtime.summary_notes,
                "memory_facts": [
                    item.to_dict()
                    for item in entry.runtime.memory_facts
                ],
                "last_seen_channel": entry.runtime.last_seen_channel,
                "last_seen_uid": entry.runtime.last_seen_uid,
            },
        }

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_all(self, manager: UserMemoryManager) -> None:
        """Save all user memories using group-isolated layout."""
        for group_id, group_entries in manager.entries.items():
            group_dir = self._base_dir / "groups" / group_id
            group_dir.mkdir(parents=True, exist_ok=True)
            for user_id, entry in group_entries.items():
                file_name = f"{self._safe_filename(user_id)}.json"
                target = group_dir / file_name
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_text(
                    json.dumps(self._entry_to_payload(entry), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(target)

    def save_group(self, manager: UserMemoryManager, group_id: str) -> None:
        """Save a specific group's user memories."""
        group_entries = manager.entries.get(group_id)
        if not group_entries:
            return
        group_dir = self._base_dir / "groups" / group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        for user_id, entry in group_entries.items():
            file_name = f"{self._safe_filename(user_id)}.json"
            target = group_dir / file_name
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._entry_to_payload(entry), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(target)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_all(self) -> UserMemoryManager:
        """Load all user memories from group-isolated layout.

        Automatically triggers v0.28 migration if old-format files are detected.
        """
        from sirius_chat.memory.migration.v0_28_group_isolation import (
            migrate_workspace,
            detect_old_format,
        )

        # Auto-migrate old format on first load
        if detect_old_format(self._base_dir):
            work_path = self._base_dir.parent
            migrate_workspace(work_path)

        manager = UserMemoryManager()
        groups_dir = self._base_dir / "groups"
        if not groups_dir.exists():
            return manager

        for group_dir in groups_dir.iterdir():
            if not group_dir.is_dir():
                continue
            group_id = group_dir.name
            for file_path in group_dir.glob("*.json"):
                if file_path.name == "group_state.json":
                    continue
                self._load_file(file_path, manager, group_id)

        return manager

    def load_group(self, group_id: str) -> dict[str, UserMemoryEntry]:
        """Load a specific group's user memories."""
        entries: dict[str, UserMemoryEntry] = {}
        manager = UserMemoryManager()
        group_dir = self._base_dir / "groups" / group_id
        if not group_dir.exists():
            return entries
        for file_path in group_dir.glob("*.json"):
            if file_path.name == "group_state.json":
                continue
            self._load_file(file_path, manager, group_id)
        return manager.entries.get(group_id, {})

    def _load_file(
        self,
        file_path: Path,
        manager: UserMemoryManager,
        group_id: str,
    ) -> None:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        profile_data = payload.get("profile", {})
        if not isinstance(profile_data, dict):
            return

        user_id = str(profile_data.get("user_id", "")).strip()
        if not user_id:
            return

        from sirius_chat.memory.user.models import UserProfile
        profile = UserProfile(
            user_id=user_id,
            name=str(profile_data.get("name", user_id)).strip() or user_id,
            persona=str(profile_data.get("persona", "")).strip(),
            identities=dict(profile_data.get("identities", {})),
            aliases=list(profile_data.get("aliases", [])),
            traits=list(profile_data.get("traits", [])),
            metadata=dict(profile_data.get("metadata", {})),
        )
        manager.register_user(profile, group_id=group_id)

        runtime_data = payload.get("runtime", {})
        if not isinstance(runtime_data, dict):
            return

        entry = manager.entries[group_id][user_id]
        entry.runtime.inferred_persona = str(runtime_data.get("inferred_persona", "")).strip()
        entry.runtime.inferred_traits = list(runtime_data.get("inferred_traits", []))
        entry.runtime.preference_tags = list(runtime_data.get("preference_tags", []))
        entry.runtime.recent_messages = list(runtime_data.get("recent_messages", []))
        entry.runtime.summary_notes = list(runtime_data.get("summary_notes", []))
        entry.runtime.memory_facts = [
            MemoryFact.from_dict(item)
            for item in list(runtime_data.get("memory_facts", []))
            if isinstance(item, dict) and str(item.get("value", "")).strip()
        ]
        entry.runtime.last_seen_channel = str(runtime_data.get("last_seen_channel", "")).strip()
        entry.runtime.last_seen_uid = str(runtime_data.get("last_seen_uid", "")).strip()
