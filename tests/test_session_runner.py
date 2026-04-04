from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sirius_chat.api import Agent, AgentPreset, JsonPersistentSessionRunner, Participant, SessionConfig
from sirius_chat.providers.mock import MockProvider
from sirius_chat.session_store import SqliteSessionStore


def test_json_persistent_session_runner_auto_persistence_and_reset(tmp_path: Path) -> None:
    async def _run() -> None:
        config = SessionConfig(
            work_path=tmp_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="runner-test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )
        runner = JsonPersistentSessionRunner(
            config=config,
            provider=MockProvider(responses=["回复1", "回复2"]),
        )

        await runner.initialize(primary_user=Participant(name="小王", user_id="u_wang", persona="产品经理"))
        msg = await runner.send_user_message("我是产品经理，关注成本和灰度")

        assert msg.content == "回复1"
        profile_path = tmp_path / "primary_user.json"
        state_path = tmp_path / "session_state.json"
        assert profile_path.exists()
        assert state_path.exists()

        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        assert payload["user_id"] == "u_wang"
        assert "runtime" in payload
        # 验证记忆系统正常工作（不再依赖启发式提取的keyword matching）
        # 用户消息已被记录到 recent_messages
        assert len(payload["runtime"]["recent_messages"]) > 0

        await runner.reset_primary_user(Participant(name="小李", user_id="u_li"), clear_transcript=True)
        assert not state_path.exists()
        payload2 = json.loads(profile_path.read_text(encoding="utf-8"))
        assert payload2["user_id"] == "u_li"

    asyncio.run(_run())


def test_json_persistent_session_runner_reuses_saved_profile(tmp_path: Path) -> None:
    async def _run() -> None:
        profile_path = tmp_path / "primary_user.json"
        profile_path.write_text(
            json.dumps(
                {
                    "name": "预置用户",
                    "user_id": "preset_user",
                    "persona": "稳定",
                    "aliases": ["小预置"],
                    "traits": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        config = SessionConfig(
            work_path=tmp_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="runner-test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )
        runner = JsonPersistentSessionRunner(config=config, provider=MockProvider(responses=["ok"]))
        await runner.initialize(resume=False)

        assert runner.primary_user is not None
        assert runner.primary_user.user_id == "preset_user"

    asyncio.run(_run())


def test_json_persistent_session_runner_supports_sqlite_store(tmp_path: Path) -> None:
    async def _run() -> None:
        config = SessionConfig(
            work_path=tmp_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="runner-test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )
        runner = JsonPersistentSessionRunner(
            config=config,
            provider=MockProvider(responses=["sqlite-ok"]),
            session_store=SqliteSessionStore(tmp_path),
        )
        await runner.initialize(primary_user=Participant(name="小王", user_id="u_wang"))
        msg = await runner.send_user_message("hello")

        assert msg.content == "sqlite-ok"
        assert (tmp_path / "session_state.db").exists()

    asyncio.run(_run())



