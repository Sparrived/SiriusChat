"""User memory file store implementation"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sirius_chat.memory.user.manager import UserMemoryManager
from sirius_chat.memory.user.models import MemoryFact, UserMemoryEntry

logger = logging.getLogger(__name__)


class UserMemoryFileStore:
    """File-based storage for user memory."""
    
    def __init__(self, work_path: Path) -> None:
        self._dir = Path(work_path) / "users"

    @property
    def directory(self) -> Path:
        return self._dir

    @staticmethod
    def _safe_filename(user_id: str) -> str:
        """Generate safe filename from user ID."""
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", user_id.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "user"

    @staticmethod
    def _entry_to_payload(entry: UserMemoryEntry) -> dict[str, Any]:
        """Convert entry to serializable payload."""
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

    def save_all(self, manager: UserMemoryManager) -> None:
        """Save all user memories to files."""
        self._dir.mkdir(parents=True, exist_ok=True)
        for user_id, entry in manager.entries.items():
            file_name = f"{self._safe_filename(user_id)}.json"
            target = self._dir / file_name
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(self._entry_to_payload(entry), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)

    def load_all(self) -> UserMemoryManager:
        """Load all user memories from files."""
        manager = UserMemoryManager()
        if not self._dir.exists():
            return manager

        for file_path in self._dir.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(payload, dict):
                continue

            profile_data = payload.get("profile", {})
            if not isinstance(profile_data, dict):
                continue
            user_id = str(profile_data.get("user_id", "")).strip()
            if not user_id:
                continue

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
            manager.register_user(profile)

            runtime_data = payload.get("runtime", {})
            if not isinstance(runtime_data, dict):
                continue
            entry = manager.entries[user_id]
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
            if not entry.runtime.memory_facts:
                for note in entry.runtime.summary_notes:
                    value = str(note).strip()
                    if not value:
                        continue
                    entry.runtime.memory_facts.append(
                        MemoryFact(
                            fact_type="summary",
                            value=value,
                            source="legacy",
                            confidence=0.4,
                            observed_at="",
                        )
                    )
            entry.runtime.last_seen_channel = str(runtime_data.get("last_seen_channel", "")).strip()
            entry.runtime.last_seen_uid = str(runtime_data.get("last_seen_uid", "")).strip()

        # Schema write-back: persist any new default fields to each user file.
        self.save_all(manager)
        return manager
