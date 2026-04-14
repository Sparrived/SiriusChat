from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import Agent, AgentPreset, SessionConfig
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.session.store import SessionStore, SessionStoreFactory, SqliteSessionStore
from sirius_chat.workspace.runtime import WorkspaceRuntime


PRIMARY_USER_FILE_NAME = "primary_user.json"


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _participant_to_payload(participant: Participant) -> dict[str, object]:
    return {
        "name": participant.name,
        "user_id": participant.user_id,
        "persona": participant.persona,
        "aliases": participant.aliases,
        "traits": participant.traits,
    }


def _payload_to_participant(payload: dict[str, object]) -> Participant:
    aliases_raw = payload.get("aliases", [])
    traits_raw = payload.get("traits", [])
    return Participant(
        name=str(payload.get("name", "用户")),
        user_id=str(payload.get("user_id", payload.get("name", "用户"))),
        persona=str(payload.get("persona", "")),
        aliases=list(aliases_raw) if isinstance(aliases_raw, list) else [],
        traits=list(traits_raw) if isinstance(traits_raw, list) else [],
    )


def _clone_session_config(config: SessionConfig, *, session_id: str) -> SessionConfig:
    return SessionConfig(
        work_path=config.work_path,
        data_path=config.data_path,
        preset=AgentPreset(
            agent=Agent(
                name=config.agent.name,
                persona=config.agent.persona,
                model=config.agent.model,
                temperature=config.agent.temperature,
                max_tokens=config.agent.max_tokens,
                metadata=dict(config.agent.metadata),
            ),
            global_system_prompt=config.global_system_prompt,
        ),
        history_max_messages=config.history_max_messages,
        history_max_chars=config.history_max_chars,
        max_recent_participant_messages=config.max_recent_participant_messages,
        enable_auto_compression=config.enable_auto_compression,
        orchestration=config.orchestration,
        session_id=session_id,
    )


@dataclass(slots=True)
class JsonPersistentSessionRunner:
    """High-level async runner with automatic JSON persistence.

    Responsibilities:
    - Manage primary user profile persistence.
    - Manage transcript load/save around each turn.
    - Expose simple send/reset APIs for application callers.
    """

    config: SessionConfig
    provider: LLMProvider | AsyncLLMProvider
    work_path: Path | None = None
    session_store: SessionStore | None = None
    engine: AsyncRolePlayEngine = field(init=False)
    store: SessionStore = field(init=False)
    transcript: Transcript | None = field(default=None, init=False)
    primary_user: Participant | None = field(default=None, init=False)
    runtime: WorkspaceRuntime = field(init=False)
    session_id: str = field(default="default", init=False)

    def __post_init__(self) -> None:
        base = Path(self.work_path) if self.work_path else self.config.data_path
        self.work_path = base
        self.work_path.mkdir(parents=True, exist_ok=True)
        self.config.data_path = self.work_path
        self.engine = AsyncRolePlayEngine(self.provider)
        store_factory = SessionStoreFactory(fixed_store=self.session_store) if self.session_store is not None else SessionStoreFactory()
        self.runtime = WorkspaceRuntime.open(
            self.work_path,
            config_path=self.config.work_path,
            provider=self.provider,
            store_factory=store_factory,
            session_config_factory=lambda session_id: _clone_session_config(self.config, session_id=session_id),
        )
        self.store = self.runtime.get_session_store(self.session_id)

    @property
    def _primary_user_path(self) -> Path:
        assert self.work_path is not None
        return self.work_path / PRIMARY_USER_FILE_NAME

    def _set_primary_user(self, participant: Participant) -> None:
        self.primary_user = participant

    def _load_primary_user_from_disk(self) -> Participant | None:
        if not self._primary_user_path.exists():
            return None
        payload = json.loads(self._primary_user_path.read_text(encoding="utf-8"))
        return _payload_to_participant(payload)

    def _persist_primary_user(self) -> None:
        if self.primary_user is None:
            return
        payload = _participant_to_payload(self.primary_user)
        runtime_entry = None
        if self.transcript is not None:
            runtime_entry = self.transcript.user_memory.entries.get(self.primary_user.user_id)
            if runtime_entry is None:
                resolved_user_id = self.transcript.user_memory.resolve_user_id(speaker=self.primary_user.name)
                if resolved_user_id:
                    runtime_entry = self.transcript.user_memory.entries.get(resolved_user_id)

        if runtime_entry is not None:
            runtime = runtime_entry.runtime
            payload["runtime"] = {
                "inferred_persona": runtime.inferred_persona,
                "inferred_traits": runtime.inferred_traits,
                "preference_tags": runtime.preference_tags,
                "recent_messages": runtime.recent_messages,
                "summary_notes": runtime.summary_notes,
                "last_seen_channel": runtime.last_seen_channel,
                "last_seen_uid": runtime.last_seen_uid,
            }
        _atomic_write_json(self._primary_user_path, payload)

    async def initialize(self, *, primary_user: Participant | None = None, resume: bool = True) -> None:
        await self.runtime.initialize()
        self.store = self.runtime.get_session_store(self.session_id)
        if resume:
            self.transcript = await self.runtime.get_transcript(self.session_id)

        existing = self._load_primary_user_from_disk()
        if existing is None:
            existing = await self.runtime.get_primary_user(self.session_id)
        chosen = existing or primary_user or self.primary_user
        if chosen is None:
            raise ValueError("primary_user is required for first initialization.")

        self._set_primary_user(chosen)
        await self.runtime.set_primary_user(self.session_id, chosen)
        self._persist_primary_user()

    async def send_user_message(self, content: str) -> Message:
        if self.primary_user is None:
            raise RuntimeError("Runner not initialized. Call initialize() first.")

        self.transcript = await self.runtime.run_live_message(
            session_id=self.session_id,
            turn=Message(role="user", speaker=self.primary_user.name, content=content),
            user_profile=self.primary_user.as_user_profile(),
        )
        self.store = self.runtime.get_session_store(self.session_id)
        self._persist_primary_user()
        return self.transcript.messages[-1]

    async def reset_primary_user(self, participant: Participant, *, clear_transcript: bool = True) -> None:
        self._set_primary_user(participant)
        if clear_transcript:
            self.transcript = None
            await self.runtime.clear_session(self.session_id)
        await self.runtime.set_primary_user(self.session_id, participant)
        self._persist_primary_user()
