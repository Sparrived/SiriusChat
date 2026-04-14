from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

from sirius_chat.api import Agent, AgentPreset, JsonPersistentSessionRunner, Participant, SessionConfig
from sirius_chat.config import OrchestrationPolicy, TokenUsageRecord
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.mock import MockProvider
from sirius_chat.session.store import SqliteSessionStore


def test_json_persistent_session_runner_auto_persistence_and_reset(tmp_path: Path) -> None:
    async def _run() -> None:
        config = SessionConfig(
            work_path=tmp_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="runner-test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "event_extract": False,
                },
            message_debounce_seconds=0.0,
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
        state_path = tmp_path / "sessions" / "default" / "session_state.db"
        participants_path = tmp_path / "sessions" / "default" / "participants.json"
        assert profile_path.exists()
        assert state_path.exists()
        assert participants_path.exists()

        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        assert payload["user_id"] == "u_wang"
        assert "runtime" in payload
        # 验证记忆系统正常工作（不再依赖启发式提取的keyword matching）
        # 用户消息已被记录到 recent_messages
        assert len(payload["runtime"]["recent_messages"]) > 0

        await runner.reset_primary_user(Participant(name="小李", user_id="u_li"), clear_transcript=True)
        assert not runner.store.exists()  # session state cleared (rows gone)
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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "event_extract": False,
                },
            message_debounce_seconds=0.0,
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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "event_extract": False,
                },
            message_debounce_seconds=0.0,
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


# ---------------------------------------------------------------------------
# SQLite 持久化基准（原 test_session_store.py）
# ---------------------------------------------------------------------------


def test_sqlite_session_store_save_and_load(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path)
    transcript = Transcript(
        messages=[
            Message(
                role="user", speaker="A", content="hello",
                multimodal_inputs=[{"type": "image", "value": "https://example.com/a.png"}],
            )
        ]
    )
    participant = Participant(name="A", user_id="user_a", persona="测试用户")
    transcript.remember_participant(
        participant=participant,
        content="hello",
        max_recent_messages=5,
        channel="cli",
        channel_user_id="user_a",
    )
    transcript.user_memory.add_memory_fact(
        user_id="user_a",
        fact_type="preference",
        value="偏好茶饮",
        source="test",
        confidence=0.9,
        context_channel="cli",
    )
    transcript.user_memory.entries["user_a"].runtime.inferred_persona = "谨慎"
    transcript.reply_runtime.user_last_turn_at["user_a"] = "2026-04-14T10:00:00"
    transcript.reply_runtime.group_recent_turn_timestamps = ["2026-04-14T10:00:00"]
    transcript.reply_runtime.last_assistant_reply_at = "2026-04-14T10:00:03"
    transcript.reply_runtime.assistant_reply_timestamps = ["2026-04-14T10:00:03"]
    transcript.session_summary = "历史摘要"
    transcript.orchestration_stats = {
        "memory_extract": {"attempted": 1, "succeeded": 1},
    }
    transcript.add_token_usage_record(
        TokenUsageRecord(
            actor_id="assistant", task_name="chat_main", model="main-model",
            prompt_tokens=40, completion_tokens=20, total_tokens=60, retries_used=1,
        )
    )

    store.save(transcript)
    assert store.exists()
    loaded = store.load()
    assert loaded.messages[-1].content == "hello"
    assert loaded.messages[-1].speaker == "A"
    assert loaded.messages[-1].multimodal_inputs == [{"type": "image", "value": "https://example.com/a.png"}]
    assert loaded.session_summary == "历史摘要"
    assert loaded.orchestration_stats["memory_extract"]["succeeded"] == 1
    assert len(loaded.token_usage_records) == 1
    assert loaded.token_usage_records[0].total_tokens == 60
    assert loaded.token_usage_records[0].retries_used == 1
    assert loaded.reply_runtime.user_last_turn_at["user_a"] == "2026-04-14T10:00:00"
    assert loaded.reply_runtime.last_assistant_reply_at == "2026-04-14T10:00:03"
    assert loaded.user_memory.entries["user_a"].profile.name == "A"
    assert loaded.user_memory.entries["user_a"].runtime.inferred_persona == "谨慎"
    assert any(
        fact.value == "偏好茶饮"
        for fact in loaded.user_memory.entries["user_a"].runtime.memory_facts
    )


def test_sqlite_session_store_migrates_legacy_json_snapshot(tmp_path: Path) -> None:
    legacy_transcript = Transcript(
        messages=[Message(role="user", speaker="迁移用户", content="legacy json")],
    )
    legacy_transcript.session_summary = "legacy-summary"
    legacy_json_path = tmp_path / "session_state.json"
    legacy_json_path.write_text(
        json.dumps(legacy_transcript.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    store = SqliteSessionStore(tmp_path)

    assert store.exists()
    loaded = store.load()
    assert loaded.messages[0].content == "legacy json"
    assert loaded.session_summary == "legacy-summary"


def test_sqlite_session_store_does_not_reimport_legacy_json_after_clear(tmp_path: Path) -> None:
    legacy_transcript = Transcript(
        messages=[Message(role="user", speaker="迁移用户", content="legacy json")],
    )
    (tmp_path / "session_state.json").write_text(
        json.dumps(legacy_transcript.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    store = SqliteSessionStore(tmp_path)
    assert store.exists()
    store.clear()
    assert not store.exists()

    reopened = SqliteSessionStore(tmp_path)
    assert not reopened.exists()


def test_sqlite_session_store_migrates_legacy_payload_table_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / "session_state.db"
    legacy_transcript = Transcript(
        messages=[Message(role="assistant", speaker="Sirius", content="legacy sqlite")],
    )
    legacy_transcript.orchestration_stats = {"chat_main": {"attempted": 1, "succeeded": 1}}

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE session_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO session_state(id, payload) VALUES(1, ?)",
            (json.dumps(legacy_transcript.to_dict(), ensure_ascii=False),),
        )

    store = SqliteSessionStore(tmp_path)
    loaded = store.load()

    assert loaded.messages[0].content == "legacy sqlite"
    assert loaded.orchestration_stats["chat_main"]["succeeded"] == 1

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "session_state" not in tables
    assert "session_messages" in tables



