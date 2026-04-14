from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

from sirius_chat.api import Message, UserProfile, WorkspaceLayout, WorkspaceRuntime
from sirius_chat.config import ConfigManager
from sirius_chat.config.jsonc import load_json_document, write_session_config_jsonc
from sirius_chat.config.models import WorkspaceBootstrap
from sirius_chat.models import Transcript
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest
from sirius_chat.providers.mock import MockProvider
from sirius_chat.providers.routing import AutoRoutingProvider, ProviderConfig, ProviderRegistry


async def _wait_for_active_agent(runtime: WorkspaceRuntime, expected_agent_key: str) -> None:
    for _ in range(50):
        workspace_config = runtime.workspace_config
        if workspace_config is not None and workspace_config.active_agent_key == expected_agent_key:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"workspace config did not refresh to agent {expected_agent_key}")


def _write_generated_agents(work_path: Path, *, key: str = "main_agent") -> None:
    _write_workspace_agents(work_path, selected_key=key)


def _write_workspace_agents(
    config_root: Path,
    *,
    data_root: Path | None = None,
    selected_key: str = "main_agent",
    agents_payload: dict[str, object] | None = None,
) -> None:
    runtime_root = data_root or config_root
    layout = WorkspaceLayout(runtime_root, config_path=config_root)
    layout.ensure_directories()
    payload = agents_payload or {
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
    workspace_config = manager.load_workspace_config(config_root, data_path=runtime_root)
    workspace_config.active_agent_key = selected_key
    workspace_config.orchestration_defaults = {
        "message_debounce_seconds": 0.0,
        "task_enabled": {
            "memory_extract": False,
            "event_extract": False,
        },
    }
    manager.save_workspace_config(config_root, workspace_config, data_path=runtime_root)


def test_workspace_runtime_auto_persists_transcript_and_participants(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["第一轮回复"]))
        try:
            transcript = await runtime.run_live_message(
                session_id="group:123",
                turn=Message(role="user", speaker="Alice", content="你好"),
                user_profile=UserProfile(user_id="alice_1", name="Alice"),
            )

            layout = WorkspaceLayout(tmp_path)
            assert transcript.messages[-1].content == "第一轮回复"
            assert layout.session_store_path("group:123").exists()
            payload = json.loads(layout.session_participants_path("group:123").read_text(encoding="utf-8"))
            assert payload["primary_user_id"] == "alice_1"
            assert payload["participants"][0]["name"] == "Alice"
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_separates_config_root_and_data_root(tmp_path: Path) -> None:
    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(config_root, data_root=data_root)

        runtime = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            provider=MockProvider(responses=["分离路径回复"]),
        )
        try:
            transcript = await runtime.run_live_message(
                session_id="split-paths",
                turn=Message(role="user", speaker="Alice", content="你好"),
                user_profile=UserProfile(user_id="alice", name="Alice"),
            )

            layout = WorkspaceLayout(data_root, config_path=config_root)
            assert transcript.messages[-1].content == "分离路径回复"
            assert layout.workspace_manifest_path().exists()
            assert layout.generated_agents_path().exists()
            assert layout.session_store_path("split-paths").exists()
            assert not (data_root / "roleplay").exists()
            assert not (config_root / "sessions").exists()
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_applies_external_config_changes_via_file_watch(tmp_path: Path) -> None:
    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(
            config_root,
            data_root=data_root,
            agents_payload={
                "main_agent": {
                    "agent": {
                        "name": "主助手",
                        "persona": "测试人格",
                        "model": "mock-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "测试系统提示词",
                },
                "alt_agent": {
                    "agent": {
                        "name": "副助手",
                        "persona": "切换后人格",
                        "model": "mock-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "切换后系统提示词",
                },
            },
        )

        runtime = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            provider=MockProvider(responses=["第一轮回复", "第二轮回复"]),
        )
        try:
            first = await runtime.run_live_message(
                session_id="reload-config",
                turn=Message(role="user", speaker="Alice", content="第一句"),
            )
            assert first.messages[-1].speaker == "主助手"

            manager = ConfigManager(base_path=config_root)
            workspace_config = manager.load_workspace_config(config_root, data_path=data_root)
            workspace_config.active_agent_key = "alt_agent"
            manager.save_workspace_config(config_root, workspace_config, data_path=data_root)

            await _wait_for_active_agent(runtime, "alt_agent")

            second = await runtime.run_live_message(
                session_id="reload-config",
                turn=Message(role="user", speaker="Alice", content="第二句"),
            )

            assistant_messages = [item for item in second.messages if item.role == "assistant"]
            assert len(assistant_messages) == 2
            assert assistant_messages[-1].speaker == "副助手"
            assert assistant_messages[-1].content == "第二轮回复"
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_manual_workspace_manifest_edit_survives_restart(tmp_path: Path) -> None:
    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(
            config_root,
            data_root=data_root,
            agents_payload={
                "main_agent": {
                    "agent": {
                        "name": "主助手",
                        "persona": "测试人格",
                        "model": "mock-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "测试系统提示词",
                },
                "alt_agent": {
                    "agent": {
                        "name": "副助手",
                        "persona": "手改后人格",
                        "model": "mock-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "手改后系统提示词",
                },
            },
        )

        runtime = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            provider=MockProvider(responses=["第一轮回复"]),
        )
        try:
            await runtime.initialize()
        finally:
            await runtime.close()

        layout = WorkspaceLayout(data_root, config_path=config_root)
        manifest_path = layout.workspace_manifest_path()
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_payload["active_agent_key"] = "alt_agent"
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        session_config_path = layout.session_config_path()
        newer_ns = session_config_path.stat().st_mtime_ns + 1_000_000
        os.utime(manifest_path, ns=(newer_ns, newer_ns))

        reopened = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            provider=MockProvider(responses=["第二轮回复"]),
        )
        try:
            await reopened.initialize()
            workspace_config = reopened.workspace_config
            assert workspace_config is not None
            assert workspace_config.active_agent_key == "alt_agent"

            transcript = await reopened.run_live_message(
                session_id="manual-manifest-edit",
                turn=Message(role="user", speaker="Alice", content="你好"),
            )
            assert transcript.messages[-1].speaker == "副助手"
        finally:
            await reopened.close()

    asyncio.run(_run())


def test_workspace_runtime_reloads_provider_registry_models_after_manual_edit(tmp_path: Path) -> None:
    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(
            config_root,
            data_root=data_root,
            agents_payload={
                "main_agent": {
                    "agent": {
                        "name": "主助手",
                        "persona": "测试人格",
                        "model": "old-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "测试系统提示词",
                },
                "alt_agent": {
                    "agent": {
                        "name": "副助手",
                        "persona": "切换后人格",
                        "model": "new-model",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "切换后系统提示词",
                },
            },
        )

        layout = WorkspaceLayout(data_root, config_path=config_root)
        registry = ProviderRegistry(layout)
        registry.save(
            {
                "openai-compatible": ProviderConfig(
                    provider_type="openai-compatible",
                    api_key="test-key",
                    base_url="https://api.openai.com",
                    healthcheck_model="old-model",
                    models=["old-model"],
                )
            }
        )

        runtime = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            provider=AutoRoutingProvider(registry.load()),
        )
        try:
            with patch(
                "sirius_chat.providers.openai_compatible.OpenAICompatibleProvider.generate",
                side_effect=["第一轮回复", "第二轮回复"],
            ):
                first = await runtime.run_live_message(
                    session_id="provider-reload",
                    turn=Message(role="user", speaker="Alice", content="第一句"),
                )
                assert first.messages[-1].content == "第一轮回复"

                provider_payload = json.loads(layout.provider_registry_path().read_text(encoding="utf-8"))
                provider_payload["providers"]["openai-compatible"]["models"].append("new-model")
                layout.provider_registry_path().write_text(
                    json.dumps(provider_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                manager = ConfigManager(base_path=config_root)
                workspace_config = manager.load_workspace_config(config_root, data_path=data_root)
                workspace_config.active_agent_key = "alt_agent"
                manager.save_workspace_config(config_root, workspace_config, data_path=data_root)

                await _wait_for_active_agent(runtime, "alt_agent")

                second = await runtime.run_live_message(
                    session_id="provider-reload",
                    turn=Message(role="user", speaker="Alice", content="第二句"),
                )

                assistant_messages = [item for item in second.messages if item.role == "assistant"]
                assert len(assistant_messages) == 2
                assert assistant_messages[-1].speaker == "副助手"
                assert assistant_messages[-1].content == "第二轮回复"
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_restores_saved_session_on_next_open(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime1 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["第一轮回复"]))
        runtime2: WorkspaceRuntime | None = None
        try:
            await runtime1.run_live_message(
                session_id="restore-case",
                turn=Message(role="user", speaker="Alice", content="第一句"),
            )

            runtime2 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["第二轮回复"]))
            transcript = await runtime2.run_live_message(
                session_id="restore-case",
                turn=Message(role="user", speaker="Alice", content="第二句"),
            )

            assistant_messages = [item for item in transcript.messages if item.role == "assistant"]
            assert len(assistant_messages) == 2
            assert assistant_messages[-1].content == "第二轮回复"
        finally:
            await runtime1.close()
            if runtime2 is not None:
                await runtime2.close()

    asyncio.run(_run())


def test_workspace_runtime_lists_and_clears_multiple_sessions(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["r1", "r2"]))
        try:
            await runtime.run_live_message(
                session_id="group:1",
                turn=Message(role="user", speaker="Alice", content="hello-1"),
            )
            await runtime.run_live_message(
                session_id="dm:2",
                turn=Message(role="user", speaker="Bob", content="hello-2"),
            )

            sessions = await runtime.list_sessions()
            assert sessions == ["dm:2", "group:1"]

            await runtime.clear_session("group:1")
            assert await runtime.get_transcript("group:1") is None
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_delete_session_removes_session_directory(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["bye"]))
        try:
            await runtime.run_live_message(
                session_id="group:delete",
                turn=Message(role="user", speaker="Alice", content="delete me"),
            )

            layout = WorkspaceLayout(tmp_path)
            assert layout.session_dir("group:delete").exists()
            await runtime.delete_session("group:delete")
            assert not layout.session_dir("group:delete").exists()
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_bootstrap_preserves_existing_task_models_and_provider_models(tmp_path: Path) -> None:
    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(config_root, data_root=data_root)

        manager = ConfigManager(base_path=config_root)
        workspace_config = manager.load_workspace_config(config_root, data_path=data_root)
        workspace_config.orchestration_defaults = {
            "task_models": {
                "memory_extract": "memory-model",
                "intent_analysis": "intent-model",
            },
            "task_enabled": {
                "memory_extract": True,
                "event_extract": False,
                "intent_analysis": True,
            },
            "session_reply_mode": "auto",
        }
        manager.save_workspace_config(config_root, workspace_config, data_path=data_root)

        layout = WorkspaceLayout(data_root, config_path=config_root)
        ProviderRegistry(layout).save(
            {
                "openai-compatible": ProviderConfig(
                    provider_type="openai-compatible",
                    api_key="test-key",
                    base_url="https://api.openai.com",
                    healthcheck_model="mock-model",
                    models=["mock-model", "intent-model"],
                )
            }
        )

        runtime = WorkspaceRuntime.open(
            data_root,
            config_path=config_root,
            bootstrap=WorkspaceBootstrap(
                orchestration_defaults={"message_debounce_seconds": 0.0},
                provider_entries=[
                    {
                        "type": "openai-compatible",
                        "api_key": "test-key-updated",
                        "base_url": "https://api.openai.com/v1",
                    }
                ],
            ),
        )
        try:
            await runtime.initialize()
            exported = runtime.export_workspace_defaults()
            orchestration = exported["orchestration_defaults"]
            providers = ProviderRegistry(layout).load()

            assert orchestration["task_models"]["memory_extract"] == "memory-model"
            assert orchestration["task_models"]["intent_analysis"] == "intent-model"
            assert orchestration["task_enabled"]["intent_analysis"] is True
            assert orchestration["message_debounce_seconds"] == 0.0
            assert providers["openai-compatible"].api_key == "test-key-updated"
            assert providers["openai-compatible"].base_url == "https://api.openai.com/v1"
            assert providers["openai-compatible"].models == ["mock-model", "intent-model"]
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_uses_session_snapshot_task_models_when_manifest_is_newer(tmp_path: Path) -> None:
    class CaptureProvider(AsyncLLMProvider):
        def __init__(self) -> None:
            self.requests: list[tuple[str, str]] = []

        async def generate_async(self, request: GenerationRequest) -> str:
            self.requests.append((request.purpose, request.model))
            if request.purpose == "event_extract":
                return '[{"category":"experience","content":"用户提到一件事","confidence":0.8}]'
            return "主回复"

    async def _run() -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "runtime"
        _write_workspace_agents(
            config_root,
            data_root=data_root,
            agents_payload={
                "main_agent": {
                    "agent": {
                        "name": "主助手",
                        "persona": "测试人格",
                        "model": "qwen3.5-plus",
                        "temperature": 0.7,
                        "max_tokens": 512,
                        "metadata": {},
                    },
                    "global_system_prompt": "测试系统提示词",
                }
            },
        )

        manager = ConfigManager(base_path=config_root)
        workspace_config = manager.load_workspace_config(config_root, data_path=data_root)
        workspace_config.active_agent_key = "main_agent"
        workspace_config.orchestration_defaults = {
            "task_models": {"event_extract": "qwen3.5-plus"},
            "task_enabled": {
                "memory_extract": False,
                "event_extract": True,
                "intent_analysis": False,
            },
            "event_extract_batch_size": 1,
            "message_debounce_seconds": 0.0,
        }
        manager.save_workspace_config(config_root, workspace_config, data_path=data_root)

        layout = WorkspaceLayout(data_root, config_path=config_root)
        snapshot_path = layout.session_config_path()
        snapshot_payload = load_json_document(snapshot_path)
        snapshot_payload["generated_agent_key"] = "main_agent"
        snapshot_payload["orchestration"]["task_models"] = {
            "memory_extract": "deepseek-chat",
            "event_extract": "deepseek-chat",
            "intent_analysis": "deepseek-chat",
        }
        snapshot_payload["orchestration"]["task_enabled"] = {
            "memory_extract": False,
            "event_extract": True,
            "intent_analysis": False,
        }
        snapshot_payload["orchestration"]["event_extract_batch_size"] = 1
        snapshot_payload["orchestration"]["message_debounce_seconds"] = 0.0
        write_session_config_jsonc(snapshot_path, snapshot_payload)

        newer_ns = snapshot_path.stat().st_mtime_ns + 2_000_000
        os.utime(layout.workspace_manifest_path(), ns=(newer_ns, newer_ns))

        provider = CaptureProvider()
        runtime = WorkspaceRuntime.open(data_root, config_path=config_root, provider=provider)
        try:
            await runtime.run_live_message(
                session_id="repro",
                turn=Message(role="user", speaker="Alice", content="你好，我今天完成了项目复盘"),
            )

            assert provider.requests[0] == ("event_extract", "deepseek-chat")
            assert provider.requests[1] == ("chat_main", "qwen3.5-plus")
        finally:
            await runtime.close()

    asyncio.run(_run())


def test_workspace_runtime_initialization_reads_new_layout(tmp_path: Path) -> None:
    """After removing WorkspaceMigrationManager, initialize() only recognises
    files already placed under the workspace-layout directories."""
    async def _run() -> None:
        layout = WorkspaceLayout(tmp_path)
        layout.ensure_directories()

        # Place generated_agents at the canonical layout path
        layout.generated_agents_path().parent.mkdir(parents=True, exist_ok=True)
        layout.generated_agents_path().write_text(
            json.dumps(
                {
                    "selected_generated_agent": "main_agent",
                    "generated_agents": {
                        "main_agent": {
                            "agent": {
                                "name": "主助手",
                                "persona": "新布局人格",
                                "model": "mock-model",
                                "temperature": 0.7,
                                "max_tokens": 512,
                            },
                            "global_system_prompt": "新布局提示词",
                        }
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ignored"]))
        try:
            await runtime.initialize()
            assert layout.generated_agents_path().exists()

            # No pre-existing transcript → get_transcript returns None
            restored = await runtime.get_transcript("default")
            assert restored is None
        finally:
            await runtime.close()

    asyncio.run(_run())


