from __future__ import annotations

from sirius_chat.memory.semantic.models import *


class SemanticMemoryManager:
    def __init__(self, *args, **kwargs) -> None:
        self._groups: dict[str, Any] = {}
        self._users: dict[str, Any] = {}

    def get_user_profile(self, group_id: str, user_id: str):
        key = f"{group_id}:{user_id}"
        if key not in self._users:
            from sirius_chat.memory.semantic.models import UserSemanticProfile
            self._users[key] = UserSemanticProfile(user_id=user_id)
        return self._users[key]

    def save_user_profile(self, *args, **kwargs):
        pass

    def ensure_group_profile(self, group_id: str):
        if group_id not in self._groups:
            from sirius_chat.memory.semantic.models import GroupSemanticProfile
            self._groups[group_id] = GroupSemanticProfile(group_id=group_id)
        return self._groups[group_id]

    def get_group_profile(self, group_id: str):
        return self.ensure_group_profile(group_id)

    def list_group_user_profiles(self, *args, **kwargs):
        return []
