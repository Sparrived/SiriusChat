from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote


_DEFAULT_SESSION_FILENAME_SQLITE = "session_state.db"
_DEFAULT_SESSION_FILENAME_JSON = "session_state.json"


@dataclass(slots=True)
class WorkspaceLayout:
    """Single authority for all workspace persistence paths."""

    work_path: Path
    layout_version: int = 1

    def __post_init__(self) -> None:
        self.work_path = Path(self.work_path)

    @property
    def root(self) -> Path:
        return self.work_path

    def workspace_manifest_path(self) -> Path:
        return self.root / "workspace.json"

    def config_dir(self) -> Path:
        return self.root / "config"

    def session_config_path(self) -> Path:
        return self.config_dir() / "session_config.json"

    def providers_dir(self) -> Path:
        return self.root / "providers"

    def provider_registry_path(self) -> Path:
        return self.providers_dir() / "provider_keys.json"

    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def session_slug(self, session_id: str) -> str:
        text = str(session_id).strip() or "default"
        return quote(text, safe="")

    def session_id_from_slug(self, slug: str) -> str:
        text = str(slug).strip()
        return unquote(text) if text else "default"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir() / self.session_slug(session_id)

    def session_store_path(self, session_id: str, *, backend: str = "sqlite") -> Path:
        normalized = backend.strip().lower()
        file_name = _DEFAULT_SESSION_FILENAME_SQLITE
        if normalized == "json":
            file_name = _DEFAULT_SESSION_FILENAME_JSON
        return self.session_dir(session_id) / file_name

    def session_participants_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "participants.json"

    def memory_dir(self) -> Path:
        return self.root / "memory"

    def user_memory_dir(self) -> Path:
        return self.memory_dir() / "users"

    def event_memory_dir(self) -> Path:
        return self.memory_dir() / "events"

    def event_memory_path(self) -> Path:
        return self.event_memory_dir() / "events.json"

    def self_memory_path(self) -> Path:
        return self.memory_dir() / "self_memory.json"

    def token_dir(self) -> Path:
        return self.root / "token"

    def token_usage_db_path(self) -> Path:
        return self.token_dir() / "token_usage.db"

    def roleplay_dir(self) -> Path:
        return self.root / "roleplay"

    def generated_agents_path(self) -> Path:
        return self.roleplay_dir() / "generated_agents.json"

    def generated_agent_trace_dir(self) -> Path:
        return self.roleplay_dir() / "generated_agent_traces"

    def skills_dir(self) -> Path:
        return self.root / "skills"

    def skill_data_dir(self) -> Path:
        return self.root / "skill_data"

    def legacy_session_store_path(self, *, backend: str = "sqlite") -> Path:
        normalized = backend.strip().lower()
        if normalized == "json":
            return self.root / _DEFAULT_SESSION_FILENAME_JSON
        return self.root / _DEFAULT_SESSION_FILENAME_SQLITE

    def legacy_primary_user_path(self) -> Path:
        return self.root / "primary_user.json"

    def legacy_provider_registry_path(self) -> Path:
        return self.root / "provider_keys.json"

    def legacy_user_memory_dir(self) -> Path:
        return self.root / "users"

    def legacy_event_memory_dir(self) -> Path:
        return self.root / "events"

    def legacy_event_memory_path(self) -> Path:
        return self.legacy_event_memory_dir() / "events.json"

    def legacy_self_memory_path(self) -> Path:
        return self.root / "self_memory.json"

    def legacy_token_usage_db_path(self) -> Path:
        return self.root / "token_usage.db"

    def legacy_generated_agents_path(self) -> Path:
        return self.root / "generated_agents.json"

    def legacy_generated_agent_trace_dir(self) -> Path:
        return self.root / "generated_agent_traces"

    def ensure_directories(self, *, session_id: str | None = None) -> None:
        directories = [
            self.root,
            self.config_dir(),
            self.providers_dir(),
            self.sessions_dir(),
            self.user_memory_dir(),
            self.event_memory_dir(),
            self.token_dir(),
            self.roleplay_dir(),
            self.generated_agent_trace_dir(),
            self.skills_dir(),
            self.skill_data_dir(),
        ]
        if session_id is not None:
            directories.append(self.session_dir(session_id))
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)