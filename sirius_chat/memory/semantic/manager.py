"""Semantic memory manager: user profiles + group norms (property-graph abstraction)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sirius_chat.memory.semantic.models import (
    AtmosphereSnapshot,
    GroupSemanticProfile,
    UserSemanticProfile,
)
from sirius_chat.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)


class SemanticMemoryManager:
    """Manages semantic memory: user semantic profiles and group semantic profiles.

    Storage layout:
        {work_path}/semantic/
            ├── users/
            │   └── {group_id}_{user_id}.json
            └── groups/
                └── {group_id}.json
    """

    def __init__(self, work_path: Path | WorkspaceLayout) -> None:
        layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(work_path)
        self._base_dir = layout.work_path / "semantic"
        self._user_dir = self._base_dir / "users"
        self._group_dir = self._base_dir / "groups"
        self._user_dir.mkdir(parents=True, exist_ok=True)
        self._group_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # User semantic profile
    # ------------------------------------------------------------------

    def get_user_profile(
        self,
        group_id: str,
        user_id: str,
    ) -> UserSemanticProfile | None:
        """Load a user's semantic profile for a specific group."""
        path = self._user_path(group_id, user_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return UserSemanticProfile.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None

    def save_user_profile(
        self,
        group_id: str,
        profile: UserSemanticProfile,
    ) -> None:
        """Save a user's semantic profile."""
        path = self._user_path(group_id, profile.user_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def update_user_profile(
        self,
        group_id: str,
        user_id: str,
        updates: dict[str, Any],
    ) -> UserSemanticProfile | None:
        """Partial update a user's semantic profile."""
        profile = self.get_user_profile(group_id, user_id)
        if profile is None:
            profile = UserSemanticProfile(user_id=user_id)
        for key, value in updates.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        self.save_user_profile(group_id, profile)
        return profile

    # ------------------------------------------------------------------
    # Group semantic profile
    # ------------------------------------------------------------------

    def get_group_profile(self, group_id: str) -> GroupSemanticProfile | None:
        """Load a group's semantic profile."""
        path = self._group_path(group_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return GroupSemanticProfile.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None

    def save_group_profile(self, profile: GroupSemanticProfile) -> None:
        """Save a group's semantic profile."""
        path = self._group_path(profile.group_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def ensure_group_profile(self, group_id: str, group_name: str = "") -> GroupSemanticProfile:
        """Get or create a group's semantic profile."""
        profile = self.get_group_profile(group_id)
        if profile is None:
            profile = GroupSemanticProfile(group_id=group_id, group_name=group_name or group_id)
            self.save_group_profile(profile)
        return profile

    def append_atmosphere(
        self,
        group_id: str,
        snapshot: AtmosphereSnapshot,
    ) -> None:
        """Append an atmosphere snapshot to a group's history."""
        profile = self.ensure_group_profile(group_id)
        profile.atmosphere_history.append(snapshot)
        # Keep last 1000 snapshots
        if len(profile.atmosphere_history) > 1000:
            profile.atmosphere_history = profile.atmosphere_history[-1000:]
        self.save_group_profile(profile)

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def list_group_user_profiles(self, group_id: str) -> list[UserSemanticProfile]:
        """List all user semantic profiles in a group."""
        prefix = f"{group_id}_"
        results = []
        for path in self._user_dir.glob("*.json"):
            if path.name.startswith(prefix):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    results.append(UserSemanticProfile.from_dict(data))
                except (OSError, json.JSONDecodeError):
                    continue
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _user_path(self, group_id: str, user_id: str) -> Path:
        safe_gid = self._safe_name(group_id)
        safe_uid = self._safe_name(user_id)
        return self._user_dir / f"{safe_gid}_{safe_uid}.json"

    def _group_path(self, group_id: str) -> Path:
        return self._group_dir / f"{self._safe_name(group_id)}.json"

    @staticmethod
    def _safe_name(name: str) -> str:
        import re
        base = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", name.strip())
        base = re.sub(r"_+", "_", base).strip("_")
        return base or "unknown"
