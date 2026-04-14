from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import SessionConfig, WorkspaceConfig
from sirius_chat.config.manager import ConfigManager
from sirius_chat.memory import UserProfile
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.providers.routing import AutoRoutingProvider, WorkspaceProviderManager
from sirius_chat.session.store import SessionStore, SessionStoreFactory
from sirius_chat.workspace.layout import WorkspaceLayout
from sirius_chat.workspace.migration import MigrationReport, WorkspaceMigrationManager


@dataclass(slots=True)
class WorkspaceRuntime:
    work_path: Path
    config_path: Path | None = None
    provider: LLMProvider | AsyncLLMProvider | None = None
    store_factory: SessionStoreFactory = field(default_factory=SessionStoreFactory)
    session_config_factory: Callable[[str], SessionConfig] | None = None
    layout: WorkspaceLayout = field(init=False)
    _config_manager: ConfigManager = field(init=False, repr=False)
    _provider_manager: WorkspaceProviderManager = field(init=False, repr=False)
    _migration_manager: WorkspaceMigrationManager = field(init=False, repr=False)
    _engine: AsyncRolePlayEngine | None = field(default=None, init=False, repr=False)
    _workspace_config: WorkspaceConfig | None = field(default=None, init=False, repr=False)
    _config_signature: str | None = field(default=None, init=False, repr=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _session_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    _transcripts: dict[str, Transcript] = field(default_factory=dict, init=False, repr=False)
    _stores: dict[str, SessionStore] = field(default_factory=dict, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)
    _last_migration_report: MigrationReport | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.work_path = Path(self.work_path)
        self.config_path = self.work_path if self.config_path is None else Path(self.config_path)
        self.layout = WorkspaceLayout(self.work_path, config_path=self.config_path)
        self._config_manager = ConfigManager(base_path=self.layout.config_root)
        self._provider_manager = WorkspaceProviderManager(self.layout)
        self._migration_manager = WorkspaceMigrationManager(self.layout)

    @classmethod
    def open(
        cls,
        work_path: Path,
        *,
        config_path: Path | None = None,
        provider: LLMProvider | AsyncLLMProvider | None = None,
        store_factory: SessionStoreFactory | None = None,
        session_config_factory: Callable[[str], SessionConfig] | None = None,
    ) -> "WorkspaceRuntime":
        return cls(
            work_path=work_path,
            config_path=config_path,
            provider=provider,
            store_factory=store_factory or SessionStoreFactory(),
            session_config_factory=session_config_factory,
        )

    @property
    def workspace_config(self):
        return self._workspace_config

    @property
    def last_migration_report(self) -> MigrationReport | None:
        return self._last_migration_report

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.layout.ensure_directories(session_id="default")
        legacy_report = self._migration_manager.detect_legacy_layout(self.work_path)
        if legacy_report.has_legacy_layout:
            self._last_migration_report = self._migration_manager.migrate(self.work_path)
        self.layout.ensure_directories(session_id="default")
        await self._refresh_workspace_config(force=True, persist_defaults=True)
        self._initialized = True

    async def run_live_message(
        self,
        *,
        session_id: str,
        turn: Message,
        environment_context: str = "",
        user_profile: UserProfile | None = None,
        on_reply: Callable[[Message], Awaitable[None]] | None = None,
        timeout: float = 0,
    ) -> Transcript:
        await self.initialize()
        await self._refresh_workspace_config()
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            session_config = self._build_session_config(session_id)
            transcript = await self._load_session_for_runtime(session_id, session_config)
            try:
                transcript = await self._get_engine().run_live_message(
                    config=session_config,
                    turn=turn,
                    transcript=transcript,
                    environment_context=environment_context,
                    user_profile=user_profile,
                    on_reply=on_reply,
                    timeout=timeout,
                    finalize_and_persist=True,
                )
            except Exception:
                self._drop_cached_session(session_id)
                raise

            self._transcripts[session_id] = transcript
            store = self.get_session_store(session_id)
            store.save(transcript)
            primary_user_id = user_profile.user_id if user_profile is not None else ""
            self._persist_participants(session_id, transcript=transcript, primary_user_id=primary_user_id)
            return transcript

    async def get_transcript(self, session_id: str) -> Transcript | None:
        await self.initialize()
        cached = self._transcripts.get(session_id)
        if cached is not None:
            return cached
        store = self.get_session_store(session_id)
        if not store.exists():
            return None
        transcript = store.load()
        self._transcripts[session_id] = transcript
        return transcript

    async def set_primary_user(self, session_id: str, participant: Participant) -> None:
        await self.initialize()
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            payload = self._load_participants_payload(session_id)
            participants_raw = payload.get("participants", [])
            if not isinstance(participants_raw, list):
                participants_raw = []
            participants = {
                str(item.get("user_id", "")).strip(): item
                for item in participants_raw
                if isinstance(item, dict) and str(item.get("user_id", "")).strip()
            }
            participants[participant.user_id] = participant.to_dict()
            payload["session_id"] = session_id
            payload["primary_user_id"] = participant.user_id
            payload["participants"] = list(participants.values())
            self._write_participants_payload(session_id, payload)

    async def get_primary_user(self, session_id: str) -> Participant | None:
        await self.initialize()
        payload = self._load_participants_payload(session_id)
        primary_user_id = str(payload.get("primary_user_id", "")).strip()
        participants_raw = payload.get("participants", [])
        if not isinstance(participants_raw, list):
            participants_raw = []
        for item in participants_raw:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id", "")).strip()
            if user_id and user_id == primary_user_id:
                return Participant.from_dict(item)
        return None

    async def clear_session(self, session_id: str) -> None:
        await self.initialize()
        lock = self._session_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            store = self.get_session_store(session_id)
            if store.exists():
                store.clear()
            self.layout.session_participants_path(session_id).unlink(missing_ok=True)
            self._drop_cached_session(session_id)

    async def delete_session(self, session_id: str) -> None:
        await self.clear_session(session_id)
        store = self._stores.pop(session_id, None)
        close = getattr(store, "close", None)
        if callable(close):
            close()
        session_dir = self.layout.session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)

    async def list_sessions(self) -> list[str]:
        await self.initialize()
        sessions: list[str] = []
        sessions_dir = self.layout.sessions_dir()
        if sessions_dir.exists():
            for child in sorted(sessions_dir.iterdir()):
                if not child.is_dir():
                    continue
                session_id = self.layout.session_id_from_slug(child.name)
                if self.layout.session_participants_path(session_id).exists():
                    sessions.append(session_id)
                    continue
                if self.get_session_store(session_id).exists():
                    sessions.append(session_id)
        fixed_store = self.store_factory.fixed_store
        if fixed_store is not None and fixed_store.exists() and "default" not in sessions:
            sessions.append("default")
        return sessions

    def get_session_store(self, session_id: str) -> SessionStore:
        store = self._stores.get(session_id)
        if store is None:
            self.layout.ensure_directories(session_id=session_id)
            store = self.store_factory.create(layout=self.layout, session_id=session_id)
            self._stores[session_id] = store
        return store

    def set_provider(self, provider: LLMProvider | AsyncLLMProvider | None) -> None:
        self.provider = provider
        self._engine = None

    def _get_engine(self) -> AsyncRolePlayEngine:
        if self._engine is not None:
            return self._engine
        provider = self.provider
        if provider is None:
            providers = self._provider_manager.load()
            if not providers:
                raise RuntimeError("当前 workspace 尚未配置可用 provider。")
            provider = AutoRoutingProvider(providers)
        self._engine = AsyncRolePlayEngine(provider)
        return self._engine

    def _build_session_config(self, session_id: str) -> SessionConfig:
        if self.session_config_factory is not None:
            return self.session_config_factory(session_id)
        return self._config_manager.build_session_config(
            work_path=self.layout.config_root,
            data_path=self.layout.data_root,
            session_id=session_id,
        )

    async def _load_session_for_runtime(
        self,
        session_id: str,
        session_config: SessionConfig,
    ) -> Transcript:
        cached = self._transcripts.get(session_id)
        transcript = cached
        if transcript is None:
            store = self.get_session_store(session_id)
            if store.exists():
                transcript = store.load()
        transcript = await self._get_engine().run_live_session(
            config=session_config,
            transcript=transcript,
        )
        self._transcripts[session_id] = transcript
        return transcript

    def _drop_cached_session(self, session_id: str) -> None:
        transcript = self._transcripts.pop(session_id, None)
        if transcript is not None and self._engine is not None:
            self._engine._live_session_contexts.pop(id(transcript), None)

    def _calculate_config_signature(self) -> str:
        digest = hashlib.sha256()
        digest.update(str(self.layout.config_root).encode("utf-8"))
        digest.update(str(self.layout.data_root).encode("utf-8"))
        for path in self.layout.config_watch_paths():
            digest.update(str(path).encode("utf-8"))
            if not path.exists():
                digest.update(b"missing")
                continue
            stat = path.stat()
            digest.update(str(stat.st_mtime_ns).encode("utf-8"))
            digest.update(str(stat.st_size).encode("utf-8"))
        return digest.hexdigest()

    async def _reset_engine_state(self) -> None:
        if self._engine is None:
            return
        for context in list(self._engine._live_session_contexts.values()):
            manager = context.subsystems.bg_task_manager
            if manager is not None:
                await manager.stop()
        self._engine._live_session_contexts.clear()
        self._engine = None

    async def _refresh_workspace_config(
        self,
        *,
        force: bool = False,
        persist_defaults: bool = False,
    ) -> None:
        async with self._refresh_lock:
            signature = self._calculate_config_signature()
            if not force and self._config_signature == signature:
                return

            workspace_config = self._config_manager.load_workspace_config(
                self.layout.config_root,
                data_path=self.layout.data_root,
            )
            if persist_defaults:
                self._config_manager.save_workspace_config(
                    self.layout.config_root,
                    workspace_config,
                    data_path=self.layout.data_root,
                )
                signature = self._calculate_config_signature()

            if self._config_signature is not None and self._config_signature != signature:
                await self._reset_engine_state()

            self._workspace_config = workspace_config
            self._config_signature = signature

    def _load_participants_payload(self, session_id: str) -> dict[str, object]:
        path = self.layout.session_participants_path(session_id)
        if not path.exists():
            return {"session_id": session_id, "primary_user_id": "", "participants": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"session_id": session_id, "primary_user_id": "", "participants": []}
        if not isinstance(payload, dict):
            return {"session_id": session_id, "primary_user_id": "", "participants": []}
        return payload

    def _write_participants_payload(self, session_id: str, payload: dict[str, object]) -> None:
        path = self.layout.session_participants_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist_participants(
        self,
        session_id: str,
        *,
        transcript: Transcript,
        primary_user_id: str = "",
    ) -> None:
        existing = self._load_participants_payload(session_id)
        resolved_primary_user_id = primary_user_id or str(existing.get("primary_user_id", "")).strip()
        participants_raw = existing.get("participants", [])
        if not isinstance(participants_raw, list):
            participants_raw = []
        participants_by_id = {
            str(item.get("user_id", "")).strip(): item
            for item in participants_raw
            if isinstance(item, dict) and str(item.get("user_id", "")).strip()
        }
        for entry in transcript.user_memory.entries.values():
            participant = Participant(
                name=entry.profile.name,
                user_id=entry.profile.user_id,
                persona=entry.profile.persona,
                identities=dict(entry.profile.identities),
                aliases=list(entry.profile.aliases),
                traits=list(entry.profile.traits),
                metadata=dict(entry.profile.metadata),
            )
            participants_by_id[participant.user_id] = participant.to_dict()
        payload = {
            "session_id": session_id,
            "primary_user_id": resolved_primary_user_id,
            "participants": list(participants_by_id.values()),
        }
        self._write_participants_payload(session_id, payload)