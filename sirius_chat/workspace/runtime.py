from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from sirius_chat.config import SessionConfig, WorkspaceConfig
from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.config.manager import ConfigManager
from sirius_chat.config.models import SessionDefaults, WorkspaceBootstrap
from sirius_chat.memory import UserProfile
from sirius_chat.models import Message, Participant, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.providers.routing import AutoRoutingProvider, WorkspaceProviderManager
from sirius_chat.session.store import SessionStore, SessionStoreFactory
from sirius_chat.skills.executor import SkillExecutor
from sirius_chat.skills.registry import SkillRegistry
from sirius_chat.workspace.config_watcher import WorkspaceConfigWatcher
from sirius_chat.workspace.layout import WorkspaceLayout


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkspaceRuntime:
    work_path: Path
    config_path: Path | None = None
    provider: LLMProvider | AsyncLLMProvider | None = None
    store_factory: SessionStoreFactory = field(default_factory=SessionStoreFactory)
    session_config_factory: Callable[[str], SessionConfig] | None = None
    bootstrap: WorkspaceBootstrap | None = None
    persist_bootstrap: bool = True
    layout: WorkspaceLayout = field(init=False)
    _config_manager: ConfigManager = field(init=False, repr=False)
    _provider_manager: WorkspaceProviderManager = field(init=False, repr=False)
    _workspace_config: WorkspaceConfig | None = field(default=None, init=False, repr=False)
    _config_signature: str | None = field(default=None, init=False, repr=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _watch_loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _config_watcher: WorkspaceConfigWatcher | None = field(default=None, init=False, repr=False)
    _watcher_refresh_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _transcripts: dict[str, Transcript] = field(default_factory=dict, init=False, repr=False)
    _stores: dict[str, SessionStore] = field(default_factory=dict, init=False, repr=False)
    _skill_registry: SkillRegistry | None = field(default=None, init=False, repr=False)
    _skill_executor: SkillExecutor | None = field(default=None, init=False, repr=False)
    _initialized: bool = field(default=False, init=False, repr=False)
    _prefer_workspace_registry_provider: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.work_path = Path(self.work_path)
        self.config_path = self.work_path if self.config_path is None else Path(self.config_path)
        self.layout = WorkspaceLayout(self.work_path, config_path=self.config_path)
        self._config_manager = ConfigManager(base_path=self.layout.config_root)
        self._provider_manager = WorkspaceProviderManager(self.layout)
        self._prefer_workspace_registry_provider = self.provider is None or isinstance(self.provider, AutoRoutingProvider)

    @classmethod
    def open(
        cls,
        work_path: Path,
        *,
        config_path: Path | None = None,
        provider: LLMProvider | AsyncLLMProvider | None = None,
        store_factory: SessionStoreFactory | None = None,
        session_config_factory: Callable[[str], SessionConfig] | None = None,
        bootstrap: WorkspaceBootstrap | None = None,
        persist_bootstrap: bool = True,
    ) -> "WorkspaceRuntime":
        return cls(
            work_path=work_path,
            config_path=config_path,
            provider=provider,
            store_factory=store_factory or SessionStoreFactory(),
            session_config_factory=session_config_factory,
            bootstrap=bootstrap,
            persist_bootstrap=persist_bootstrap,
        )

    @property
    def workspace_config(self):
        return self._workspace_config

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.layout.ensure_directories(session_id="default")
        self._apply_bootstrap()
        await self._refresh_workspace_config(force=True, persist_defaults=True)
        self._initialized = True
        self._start_config_watcher()

    async def close(self) -> None:
        self._stop_config_watcher()
        task = self._watcher_refresh_task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for store in self._stores.values():
            close = getattr(store, "close", None)
            if callable(close):
                close()
        self._stores.clear()
        self._transcripts.clear()
        self._skill_registry = None
        self._skill_executor = None

    def __del__(self) -> None:
        self._stop_config_watcher()

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
        store = self.get_session_store(session_id)
        if store.exists():
            store.clear()
        self.layout.session_participants_path(session_id).unlink(missing_ok=True)
        self._transcripts.pop(session_id, None)

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
        self._prefer_workspace_registry_provider = provider is None or isinstance(provider, AutoRoutingProvider)

    def set_provider_entries(self, entries: list[dict[str, object]], *, persist: bool = True) -> None:
        """Inject provider config entries from the host.

        The runtime validates entries, optionally persists them to the
        workspace provider registry, and rebuilds the internal routing
        provider so that subsequent ``run_live_message`` calls use the
        new configuration.
        """
        saved = self._provider_manager.save_from_entries(entries) if persist else self._provider_manager.merge_entries(entries)
        if persist:
            self.provider = None
            self._prefer_workspace_registry_provider = True
            return
        if saved:
            self.provider = AutoRoutingProvider(saved)
            self._prefer_workspace_registry_provider = False

    def export_workspace_defaults(self) -> dict[str, object]:
        """Return the current workspace defaults as a plain dict.

        Intended for host wizard / settings UI so the caller never needs to
        understand the underlying file layout.
        """
        cfg = self._workspace_config
        if cfg is None:
            cfg = self._config_manager.load_workspace_config(
                self.layout.config_root, data_path=self.layout.data_root
            )
        return {
            "active_agent_key": cfg.active_agent_key,
            "session_defaults": {
                "history_max_messages": cfg.session_defaults.history_max_messages,
                "history_max_chars": cfg.session_defaults.history_max_chars,
                "max_recent_participant_messages": cfg.session_defaults.max_recent_participant_messages,
                "enable_auto_compression": cfg.session_defaults.enable_auto_compression,
            },
            "orchestration_defaults": dict(cfg.orchestration_defaults),
            "provider_policy": {
                "prefer_workspace_registry": cfg.provider_policy.prefer_workspace_registry,
            },
        }

    async def apply_workspace_updates(self, patch: dict[str, object]) -> WorkspaceConfig:
        """Apply a partial update to workspace defaults and persist.

        The caller provides only the fields it wants to change; the runtime
        merges them, validates, persists and triggers a hot-refresh.
        """
        await self.initialize()
        cfg = self._workspace_config
        assert cfg is not None

        if "active_agent_key" in patch and patch["active_agent_key"] is not None:
            cfg.active_agent_key = str(patch["active_agent_key"]).strip()

        sd_patch = patch.get("session_defaults")
        if isinstance(sd_patch, dict):
            for key in (
                "history_max_messages",
                "history_max_chars",
                "max_recent_participant_messages",
                "enable_auto_compression",
            ):
                if key in sd_patch and sd_patch[key] is not None:
                    setattr(
                        cfg.session_defaults,
                        key,
                        type(getattr(cfg.session_defaults, key))(sd_patch[key]),
                    )

        orch_patch = patch.get("orchestration_defaults")
        if isinstance(orch_patch, dict):
            cfg.orchestration_defaults = self._config_manager.merge_configs(
                dict(cfg.orchestration_defaults),
                dict(orch_patch),
            )

        pp_patch = patch.get("provider_policy")
        if isinstance(pp_patch, dict):
            if "prefer_workspace_registry" in pp_patch and pp_patch["prefer_workspace_registry"] is not None:
                cfg.provider_policy.prefer_workspace_registry = bool(pp_patch["prefer_workspace_registry"])

        self._config_manager.save_workspace_config(
            self.layout.config_root, cfg, data_path=self.layout.data_root
        )
        await self._refresh_workspace_config(force=True)
        return self._workspace_config  # type: ignore[return-value]

    def _apply_bootstrap(self) -> None:
        """Merge host-provided bootstrap into workspace config files."""
        bs = self.bootstrap
        if bs is None:
            return
        cfg = self._config_manager.load_workspace_config(
            self.layout.config_root, data_path=self.layout.data_root
        )
        bootstrap_signature = ""
        if self.persist_bootstrap:
            bootstrap_signature = self._calculate_bootstrap_signature(bs)
            if cfg.bootstrap_signature == bootstrap_signature:
                return
        if bs.active_agent_key:
            cfg.active_agent_key = bs.active_agent_key
        if bs.session_defaults is not None:
            cfg.session_defaults = bs.session_defaults
        if bs.orchestration_defaults is not None:
            cfg.orchestration_defaults = self._config_manager.merge_configs(
                dict(cfg.orchestration_defaults),
                dict(bs.orchestration_defaults),
            )
        if bs.provider_policy is not None:
            cfg.provider_policy = bs.provider_policy
        if self.persist_bootstrap:
            cfg.bootstrap_signature = bootstrap_signature
            self._config_manager.save_workspace_config(
                self.layout.config_root, cfg, data_path=self.layout.data_root
            )
        if bs.provider_entries:
            self.set_provider_entries(bs.provider_entries, persist=self.persist_bootstrap)

    def _calculate_bootstrap_signature(self, bootstrap: WorkspaceBootstrap) -> str:
        session_defaults = None
        if bootstrap.session_defaults is not None:
            session_defaults = {
                "history_max_messages": bootstrap.session_defaults.history_max_messages,
                "history_max_chars": bootstrap.session_defaults.history_max_chars,
                "max_recent_participant_messages": bootstrap.session_defaults.max_recent_participant_messages,
                "enable_auto_compression": bootstrap.session_defaults.enable_auto_compression,
            }
        provider_policy = None
        if bootstrap.provider_policy is not None:
            provider_policy = {
                "prefer_workspace_registry": bootstrap.provider_policy.prefer_workspace_registry,
            }
        payload = {
            "active_agent_key": (bootstrap.active_agent_key or "").strip(),
            "session_defaults": session_defaults,
            "orchestration_defaults": dict(bootstrap.orchestration_defaults or {}),
            "provider_entries": list(bootstrap.provider_entries or []),
            "provider_policy": provider_policy,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def load_skills(self, *, auto_install_deps: bool = True) -> None:
        """Discover and load SKILL registry + executor from workspace.

        Loads built-in skills first, then user-defined skills from the workspace
        ``skills/`` directory.  Safe to call multiple times (idempotent).
        """
        if self._skill_registry is not None and self._skill_executor is not None:
            return

        registry = SkillRegistry()
        builtin_loaded = registry._load_builtin_skills(auto_install_deps=auto_install_deps)
        if builtin_loaded:
            logger.info("内置 SKILL 已加载 %d 个", builtin_loaded)

        skills_dir = self.layout.config_root / "skills"
        if skills_dir.exists():
            user_loaded = registry.load_from_directory(
                skills_dir,
                auto_install_deps=auto_install_deps,
                include_builtin=False,
            )
            if user_loaded:
                logger.info("用户 SKILL 已加载 %d 个", user_loaded)

        self._skill_registry = registry
        self._skill_executor = SkillExecutor(self.work_path)
        logger.info("SKILL runtime 已就绪，共 %d 个技能", len(registry.skill_names))

    def create_emotional_engine(
        self,
        *,
        config: dict[str, Any] | None = None,
        persona: Any | None = None,
    ) -> EmotionalGroupChatEngine:
        """Create an EmotionalGroupChatEngine bound to this workspace.

        The engine shares the workspace's work_path and provider.
        Background tasks are NOT started automatically—call
        ``engine.start_background_tasks()`` after creation if desired.

        Skills are auto-loaded if not already loaded via ``load_skills()``.
        """
        provider = self.provider
        if self._prefer_workspace_registry_provider:
            providers = self._provider_manager.load()
            if providers:
                provider = AutoRoutingProvider(providers)
        provider_async = provider if provider is None or hasattr(provider, "generate_async") else None

        # Auto-load persona from roleplay assets if not provided
        if persona is None:
            try:
                from sirius_chat.core.persona_generator import PersonaGenerator
                from sirius_chat.config.models import AgentPreset
                agent_key = getattr(self._workspace_config, "active_agent_key", None)
                if agent_key:
                    preset = self._load_roleplay_preset(agent_key)
                    if preset:
                        persona = PersonaGenerator.from_roleplay_preset(preset)
                        logger.info("从角色模板里读到了 '%s' 的人格设定", persona.name)
            except Exception as exc:
                logger.debug("Failed to load persona from roleplay assets: %s", exc)

        engine = EmotionalGroupChatEngine(
            work_path=self.work_path,
            provider_async=provider_async,
            config=config,
            persona=persona,
        )

        # Auto-load skills if not already loaded
        if self._skill_registry is None or self._skill_executor is None:
            try:
                self.load_skills()
            except Exception as exc:
                logger.warning("自动加载 SKILL 失败: %s", exc)

        # Inject skill runtime if available
        if self._skill_registry is not None and self._skill_executor is not None:
            engine.set_skill_runtime(
                skill_registry=self._skill_registry,
                skill_executor=self._skill_executor,
            )
        return engine

    def _load_roleplay_preset(self, agent_key: str) -> Any | None:
        """Load roleplay preset from generated_agents.json."""
        try:
            import json
            agents_path = self.layout.config_root / "roleplay" / "generated_agents.json"
            if not agents_path.exists():
                return None
            data = json.loads(agents_path.read_text(encoding="utf-8"))
            agents = data.get("generated_agents", {})
            agent_data = agents.get(agent_key)
            if agent_data:
                from sirius_chat.config.models import Agent, AgentPreset
                agent = Agent(**agent_data.get("agent", {}))
                return AgentPreset(
                    agent=agent,
                    global_system_prompt=agent_data.get("global_system_prompt", ""),
                )
        except Exception:
            pass
        return None

    def _workspace_auto_install_skill_deps(self) -> bool:
        workspace = self._workspace_config
        orchestration_defaults = workspace.orchestration_defaults if workspace is not None else {}
        return bool(orchestration_defaults.get("auto_install_skill_deps", True))

    def _build_session_config(self, session_id: str) -> SessionConfig:
        if self.session_config_factory is not None:
            return self.session_config_factory(session_id)
        return self._config_manager.build_session_config(
            work_path=self.layout.config_root,
            data_path=self.layout.data_root,
            session_id=session_id,
        )

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

    async def _refresh_workspace_config(
        self,
        *,
        force: bool = False,
        persist_defaults: bool = False,
        suppress_errors: bool = False,
    ) -> None:
        async with self._refresh_lock:
            signature = self._calculate_config_signature()
            if not force and self._config_signature == signature:
                return

            try:
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
            except Exception:
                if suppress_errors:
                    logger.warning("检测到配置文件变更，但刷新 workspace 配置失败。", exc_info=True)
                    return
                raise

            self._workspace_config = workspace_config
            self._config_signature = signature

    def _start_config_watcher(self) -> None:
        if self._config_watcher is not None:
            return
        try:
            self._watch_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        watcher = WorkspaceConfigWatcher(
            watched_paths=self.layout.config_watch_paths(),
            on_change=self._handle_config_change,
        )
        if not watcher.start():
            return
        self._config_watcher = watcher

    def _stop_config_watcher(self) -> None:
        watcher = self._config_watcher
        self._config_watcher = None
        self._watch_loop = None
        if watcher is not None:
            watcher.stop()

        task = self._watcher_refresh_task
        self._watcher_refresh_task = None
        if task is not None and not task.done():
            task.cancel()

    def _handle_config_change(self, changed_path: Path) -> None:
        loop = self._watch_loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._schedule_watcher_refresh, changed_path)

    def _schedule_watcher_refresh(self, changed_path: Path) -> None:
        task = self._watcher_refresh_task
        if task is not None and not task.done():
            return
        logger.info("察觉到配置文件有变化，准备更新 workspace 配置：%s", changed_path)
        self._watcher_refresh_task = asyncio.create_task(self._refresh_workspace_config_from_watch())
        self._watcher_refresh_task.add_done_callback(self._clear_watcher_refresh_task)

    async def _refresh_workspace_config_from_watch(self) -> None:
        await asyncio.sleep(0.05)
        await self._refresh_workspace_config(suppress_errors=True)

    def _clear_watcher_refresh_task(self, task: asyncio.Task[None]) -> None:
        if self._watcher_refresh_task is task:
            self._watcher_refresh_task = None
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.warning("监听配置文件后的刷新任务失败。", exc_info=exception)

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
        for group_entries in transcript.user_memory.entries.values():
            for entry in group_entries.values():
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