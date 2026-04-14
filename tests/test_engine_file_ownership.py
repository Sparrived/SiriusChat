"""Tests for Engine File Ownership APIs (v0.25.0).

Covers:
- WorkspaceBootstrap via open_workspace_runtime()
- set_provider_entries()
- export_workspace_defaults()
- apply_workspace_updates()
- RoleplayWorkspaceManager.bootstrap_active_agent()
- RoleplayWorkspaceManager.bootstrap_from_legacy_session_config()
- Legacy generated_agents.json fallback read path
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sirius_chat.config import ConfigManager
from sirius_chat.config.models import (
    ProviderPolicy,
    SessionDefaults,
    WorkspaceBootstrap,
)
from sirius_chat.workspace.layout import WorkspaceLayout
from sirius_chat.workspace.roleplay_manager import RoleplayWorkspaceManager
from sirius_chat.workspace.runtime import WorkspaceRuntime
from sirius_chat.providers.mock import MockProvider


# ── helpers ─────────────────────────────────────────────────


def _write_workspace_agents(
    config_root: Path,
    *,
    selected_key: str = "main_agent",
) -> None:
    layout = WorkspaceLayout(config_root)
    layout.ensure_directories()
    payload = {
        selected_key: {
            "agent": {
                "name": "主助手",
                "persona": "测试人格",
                "model": "mock-model",
                "temperature": 0.7,
                "max_tokens": 512,
                "metadata": {},
            },
            "global_system_prompt": "测试系统提示词",
        }
    }
    layout.generated_agents_path().write_text(
        json.dumps(
            {
                "selected_generated_agent": selected_key,
                "generated_agents": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manager = ConfigManager(base_path=config_root)
    workspace_config = manager.load_workspace_config(config_root)
    workspace_config.active_agent_key = selected_key
    workspace_config.orchestration_defaults = {
        "message_debounce_seconds": 0.0,
        "task_enabled": {
            "memory_extract": False,
            "event_extract": False,
        },
    }
    manager.save_workspace_config(config_root, workspace_config)


# ── WorkspaceBootstrap ──────────────────────────────────────


def test_bootstrap_sets_active_agent_key(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        bs = WorkspaceBootstrap(active_agent_key="main_agent")
        runtime = WorkspaceRuntime.open(
            tmp_path,
            provider=MockProvider(responses=["ok"]),
            bootstrap=bs,
        )
        try:
            await runtime.initialize()
            cfg = runtime.workspace_config
            assert cfg is not None
            assert cfg.active_agent_key == "main_agent"
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_bootstrap_session_defaults_persisted(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        sd = SessionDefaults(history_max_messages=99, history_max_chars=5000)
        bs = WorkspaceBootstrap(session_defaults=sd)
        runtime = WorkspaceRuntime.open(
            tmp_path,
            provider=MockProvider(responses=["ok"]),
            bootstrap=bs,
            persist_bootstrap=True,
        )
        try:
            await runtime.initialize()
            exported = runtime.export_workspace_defaults()
            assert exported["session_defaults"]["history_max_messages"] == 99
            assert exported["session_defaults"]["history_max_chars"] == 5000
        finally:
            await runtime.close()

        # Verify persisted by re-opening without bootstrap
        runtime2 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime2.initialize()
            exported2 = runtime2.export_workspace_defaults()
            assert exported2["session_defaults"]["history_max_messages"] == 99
        finally:
            await runtime2.close()

    asyncio.run(_run())


def test_bootstrap_no_persist(tmp_path: Path) -> None:
    """persist_bootstrap=False: runtime uses bootstrap but does not save."""
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        sd = SessionDefaults(history_max_messages=123)
        bs = WorkspaceBootstrap(session_defaults=sd)
        runtime = WorkspaceRuntime.open(
            tmp_path,
            provider=MockProvider(responses=["ok"]),
            bootstrap=bs,
            persist_bootstrap=False,
        )
        try:
            await runtime.initialize()
        finally:
            await runtime.close()

        # Re-open: should NOT see the bootstrap defaults
        runtime2 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime2.initialize()
            exported = runtime2.export_workspace_defaults()
            assert exported["session_defaults"]["history_max_messages"] != 123
        finally:
            await runtime2.close()

    asyncio.run(_run())


def test_bootstrap_provider_policy(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        pp = ProviderPolicy(prefer_workspace_registry=False)
        bs = WorkspaceBootstrap(provider_policy=pp)
        runtime = WorkspaceRuntime.open(
            tmp_path,
            provider=MockProvider(responses=["ok"]),
            bootstrap=bs,
        )
        try:
            await runtime.initialize()
            exported = runtime.export_workspace_defaults()
            assert exported["provider_policy"]["prefer_workspace_registry"] is False
        finally:
            await runtime.close()

    asyncio.run(_run())


# ── export_workspace_defaults / apply_workspace_updates ─────


def test_export_workspace_defaults(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime.initialize()
            exported = runtime.export_workspace_defaults()
            assert "active_agent_key" in exported
            assert "session_defaults" in exported
            assert "orchestration_defaults" in exported
            assert "provider_policy" in exported
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_apply_workspace_updates_partial(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime.initialize()
            original = runtime.export_workspace_defaults()

            updated_cfg = await runtime.apply_workspace_updates({
                "session_defaults": {"history_max_messages": 77},
            })
            assert updated_cfg.session_defaults.history_max_messages == 77

            # unchanged fields preserved
            exported = runtime.export_workspace_defaults()
            assert exported["active_agent_key"] == original["active_agent_key"]
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_apply_workspace_updates_persists(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime.initialize()
            await runtime.apply_workspace_updates({
                "active_agent_key": "main_agent",
                "session_defaults": {"history_max_chars": 9999},
            })
        finally:
            await runtime.close()

        # Re-open — values persisted
        runtime2 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime2.initialize()
            exported = runtime2.export_workspace_defaults()
            assert exported["session_defaults"]["history_max_chars"] == 9999
        finally:
            await runtime2.close()

    asyncio.run(_run())


# ── set_provider_entries ────────────────────────────────────


def test_set_provider_entries_persist(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_workspace_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ok"]))
        try:
            await runtime.initialize()
            runtime.set_provider_entries(
                [
                    {
                        "type": "openai-compatible",
                        "api_key": "sk-test",
                        "base_url": "https://api.example.com",
                        "models": ["gpt-4o-mini"],
                    }
                ],
                persist=True,
            )
            layout = WorkspaceLayout(tmp_path)
            # provider registry file should exist after persist
            assert layout.provider_registry_path().exists()
        finally:
            await runtime.close()

    asyncio.run(_run())


# ── RoleplayWorkspaceManager ───────────────────────────────


def test_roleplay_manager_bootstrap_active_agent(tmp_path: Path) -> None:
    _write_workspace_agents(tmp_path, selected_key="main_agent")
    layout = WorkspaceLayout(tmp_path)
    mgr = RoleplayWorkspaceManager(layout)
    cfg = mgr.bootstrap_active_agent(agent_key="main_agent")
    assert cfg.active_agent_key == "main_agent"


def test_roleplay_manager_bootstrap_with_session_defaults(tmp_path: Path) -> None:
    _write_workspace_agents(tmp_path)
    layout = WorkspaceLayout(tmp_path)
    mgr = RoleplayWorkspaceManager(layout)
    sd = SessionDefaults(history_max_messages=55)
    cfg = mgr.bootstrap_active_agent(agent_key="main_agent", session_defaults=sd)
    assert cfg.session_defaults.history_max_messages == 55


def test_roleplay_manager_bootstrap_from_legacy_session_config(tmp_path: Path) -> None:
    _write_workspace_agents(tmp_path)

    legacy_config = tmp_path / "session.json"
    legacy_config.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "type": "openai-compatible",
                        "base_url": "https://api.openai.com",
                        "api_key": "test-key",
                        "models": ["gpt-4o-mini"],
                    }
                ],
                "generated_agent_key": "main_agent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    layout = WorkspaceLayout(tmp_path)
    mgr = RoleplayWorkspaceManager(layout)
    cfg = mgr.bootstrap_from_legacy_session_config(source=legacy_config)
    assert cfg.active_agent_key == "main_agent"


# ── Legacy generated_agents fallback read ───────────────────


def test_legacy_generated_agents_fallback(tmp_path: Path) -> None:
    """When generated_agents.json is at the root (legacy), it should still be found."""
    layout = WorkspaceLayout(tmp_path)
    layout.ensure_directories()

    # Write at legacy (root) path, not at layout.generated_agents_path()
    (tmp_path / "generated_agents.json").write_text(
        json.dumps(
            {
                "selected_generated_agent": "legacy_key",
                "generated_agents": {
                    "legacy_key": {
                        "agent": {
                            "name": "旧助手",
                            "persona": "旧人格",
                            "model": "mock-model",
                            "temperature": 0.7,
                            "max_tokens": 512,
                        },
                        "global_system_prompt": "旧提示词",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    from sirius_chat.roleplay_prompting import load_generated_agent_library

    agents, selected = load_generated_agent_library(tmp_path)
    assert "legacy_key" in agents
    assert selected == "legacy_key"
