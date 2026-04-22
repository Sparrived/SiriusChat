"""Simplified user data models and manager (v2 refactor)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sirius_chat.developer_profiles import metadata_declares_developer


@dataclass(slots=True)
class UserProfile:
    """Core user identity. Minimal and focused.

    Attributes:
        user_id: Unique identifier.
        name: Human-readable display name.
        aliases: Alternative names the user may go by.
        identities: Mapping from external systems (platform:external_uid).
        metadata: Additional custom metadata (e.g. is_developer).
    """

    user_id: str
    name: str
    persona: str = ""
    aliases: list[str] = field(default_factory=list)
    identities: dict[str, str] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_developer(self) -> bool:
        return metadata_declares_developer(self.metadata)


class UserManager:
    """Manages user profiles with group-isolated storage.

    Structure: {group_id: {user_id: UserProfile}}
    """

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, UserProfile]] = {}
        self._speaker_index: dict[str, str] = {}
        self._identity_index: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(label: str) -> str:
        return label.strip().lower()

    @staticmethod
    def _identity_key(platform: str, external_uid: str) -> str:
        return f"{platform.strip().lower()}:{external_uid.strip().lower()}"

    def _ensure_group(self, group_id: str) -> dict[str, UserProfile]:
        if group_id not in self.entries:
            self.entries[group_id] = {}
        return self.entries[group_id]

    def _update_indices(self, profile: UserProfile) -> None:
        for label in (profile.name, profile.user_id, *profile.aliases):
            if label:
                self._speaker_index[self._normalize(label)] = profile.user_id
        for platform, external_uid in profile.identities.items():
            if platform and external_uid:
                self._identity_index[self._identity_key(platform, external_uid)] = profile.user_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_user(self, profile: UserProfile, group_id: str = "default") -> None:
        """Register or update a user in a group."""
        if not profile.user_id:
            profile.user_id = profile.name or "unknown"

        group = self._ensure_group(group_id)
        existing = group.get(profile.user_id)

        if existing is None:
            group[profile.user_id] = profile
        else:
            if profile.name and not existing.name:
                existing.name = profile.name
            for alias in profile.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            for platform, external_uid in profile.identities.items():
                if platform and external_uid:
                    existing.identities[platform] = external_uid
            existing.metadata.update(profile.metadata)

        self._update_indices(profile)

    def resolve_user_id(
        self,
        *,
        speaker: str | None = None,
        platform: str | None = None,
        external_uid: str | None = None,
    ) -> str | None:
        """Resolve user ID from speaker name, platform identity, or external UID."""
        if platform and external_uid:
            resolved = self._identity_index.get(self._identity_key(platform, external_uid))
            if resolved:
                return resolved
        if speaker:
            return self._speaker_index.get(self._normalize(speaker))
        return None

    def get_user(self, user_id: str, group_id: str = "default") -> UserProfile | None:
        """Get user profile by exact ID within a group."""
        return self._ensure_group(group_id).get(user_id)

    def list_users(self, group_id: str = "default") -> list[UserProfile]:
        """List all users in a group."""
        return list(self._ensure_group(group_id).values())

    def ensure_user(self, *, speaker: str, group_id: str = "default") -> UserProfile:
        """Ensure a user exists, creating if necessary."""
        resolved = self.resolve_user_id(speaker=speaker)
        group = self._ensure_group(group_id)
        if resolved and resolved in group:
            return group[resolved]
        profile = UserProfile(user_id=speaker, name=speaker)
        self.register_user(profile, group_id=group_id)
        return profile

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            gid: {
                uid: {
                    "user_id": p.user_id,
                    "name": p.name,
                    "aliases": list(p.aliases),
                    "identities": dict(p.identities),
                    "metadata": dict(p.metadata),
                }
                for uid, p in group.items()
            }
            for gid, group in self.entries.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserManager":
        """Deserialize from dict."""
        mgr = cls()
        for gid, group in data.items():
            for uid, payload in group.items():
                if not isinstance(payload, dict):
                    continue
                profile = UserProfile(
                    user_id=str(payload.get("user_id", uid)),
                    name=str(payload.get("name", uid)),
                    aliases=list(payload.get("aliases", [])),
                    identities=dict(payload.get("identities", {})),
                    metadata=dict(payload.get("metadata", {})),
                )
                mgr.register_user(profile, group_id=gid)
        return mgr
