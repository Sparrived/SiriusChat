from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from sirius_chat.api import Message, UserProfile, WorkspaceLayout, WorkspaceRuntime
from sirius_chat.config import ConfigManager
from sirius_chat.models import Transcript
from sirius_chat.providers.mock import MockProvider


def _write_generated_agents(work_path: Path, *, key: str = "main_agent") -> None:
    layout = WorkspaceLayout(work_path)
    layout.ensure_directories()
    layout.generated_agents_path().write_text(
        json.dumps(
            {
                "selected_generated_agent": key,
                "generated_agents": {
                    key: {
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
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manager = ConfigManager(base_path=work_path)
    workspace_config = manager.load_workspace_config(work_path)
    workspace_config.active_agent_key = key
    workspace_config.orchestration_defaults = {
        "message_debounce_seconds": 0.0,
        "task_enabled": {
            "memory_extract": False,
            "event_extract": False,
        },
    }
    manager.save_workspace_config(work_path, workspace_config)


def test_workspace_runtime_auto_persists_transcript_and_participants(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["第一轮回复"]))

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

    asyncio.run(_run())


def test_workspace_runtime_restores_saved_session_on_next_open(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime1 = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["第一轮回复"]))
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

    asyncio.run(_run())


def test_workspace_runtime_lists_and_clears_multiple_sessions(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["r1", "r2"]))

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

    asyncio.run(_run())


def test_workspace_runtime_delete_session_removes_session_directory(tmp_path: Path) -> None:
    async def _run() -> None:
        _write_generated_agents(tmp_path)
        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["bye"]))
        await runtime.run_live_message(
            session_id="group:delete",
            turn=Message(role="user", speaker="Alice", content="delete me"),
        )

        layout = WorkspaceLayout(tmp_path)
        assert layout.session_dir("group:delete").exists()
        await runtime.delete_session("group:delete")
        assert not layout.session_dir("group:delete").exists()

    asyncio.run(_run())


def test_workspace_runtime_initialization_migrates_legacy_layout(tmp_path: Path) -> None:
    async def _run() -> None:
        legacy_generated_agents = tmp_path / "generated_agents.json"
        legacy_generated_agents.write_text(
            json.dumps(
                {
                    "selected_generated_agent": "main_agent",
                    "generated_agents": {
                        "main_agent": {
                            "agent": {
                                "name": "主助手",
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
        (tmp_path / "provider_keys.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "openai-compatible": {
                            "type": "openai-compatible",
                            "api_key": "legacy-key",
                            "base_url": "https://api.openai.com",
                            "healthcheck_model": "gpt-4o-mini",
                            "enabled": True,
                            "models": ["mock-model"],
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (tmp_path / "primary_user.json").write_text(
            json.dumps({"name": "Alice", "user_id": "alice_legacy"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        legacy_transcript = Transcript(messages=[Message(role="user", speaker="Alice", content="legacy")])
        (tmp_path / "session_state.json").write_text(
            json.dumps(legacy_transcript.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        runtime = WorkspaceRuntime.open(tmp_path, provider=MockProvider(responses=["ignored"]))
        await runtime.initialize()

        layout = WorkspaceLayout(tmp_path)
        assert layout.generated_agents_path().exists()
        assert layout.provider_registry_path().exists()
        assert layout.session_participants_path("default").exists()

        restored = await runtime.get_transcript("default")
        assert restored is not None
        assert restored.messages[0].content == "legacy"

    asyncio.run(_run())


def test_config_manager_bootstrap_workspace_from_legacy_session_json(tmp_path: Path) -> None:
    config_path = tmp_path / "session.json"
    config_path.write_text(
        json.dumps(
            {
                "generated_agent_key": "main_agent",
                "history_max_messages": 18,
                "history_max_chars": 4096,
                "max_recent_participant_messages": 3,
                "enable_auto_compression": False,
                "orchestration": {
                    "message_debounce_seconds": 0.0,
                    "task_enabled": {
                        "memory_extract": False,
                        "event_extract": False,
                    },
                },
                "providers": [
                    {
                        "type": "openai-compatible",
                        "api_key": "test-key",
                        "base_url": "https://api.openai.com",
                        "healthcheck_model": "gpt-4o-mini",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manager = ConfigManager(base_path=tmp_path)
    workspace_config, providers = manager.bootstrap_workspace_from_legacy_session_json(
        config_path,
        work_path=tmp_path,
    )

    layout = WorkspaceLayout(tmp_path)
    assert workspace_config.active_agent_key == "main_agent"
    assert workspace_config.session_defaults.history_max_messages == 18
    assert providers[0]["type"] == "openai-compatible"
    assert layout.workspace_manifest_path().exists()
    assert layout.session_config_path().exists()
    assert layout.provider_registry_path().exists()
