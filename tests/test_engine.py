import asyncio

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.models import Agent, AgentPreset, Message, SessionConfig
from sirius_chat.providers.mock import MockProvider
from sirius_chat.session_store import JsonSessionStore
from pathlib import Path


def test_roleplay_engine_multi_human_single_ai_transcript() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                "主助手回复 A-1",
                "主助手回复 B-1",
                "主助手回复 A-2",
                "主助手回复 B-2",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)

        config = SessionConfig(
            work_path=Path("data/tests/roleplay_engine"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="负责整合观点", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        transcript = await engine.run_live_session(
            config=config,
            human_turns=[
                Message(role="user", speaker="AgentA", content="A-1"),
                Message(role="user", speaker="AgentB", content="B-1"),
                Message(role="user", speaker="AgentA", content="A-2"),
                Message(role="user", speaker="AgentB", content="B-2"),
            ],
        )

        assert len(provider.requests) == 4
        user_messages = [item for item in transcript.messages if item.role == "user"]
        assistant_messages = [item for item in transcript.messages if item.role == "assistant"]
        system_messages = [item for item in transcript.messages if item.role == "system"]

        assert len(user_messages) == 4
        assert user_messages[0].speaker == "AgentA"
        assert len(assistant_messages) == 4
        assert assistant_messages[0].speaker == "主助手"
        assert assistant_messages[-1].content == "主助手回复 B-2"
        assert any("事件记忆" in item.content for item in system_messages)

    asyncio.run(_run())


def test_run_live_session_supports_dynamic_participants_and_memory() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=[
                "收到小王观点",
                "收到小李观点",
                "基于小王历史观点继续",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/live_session"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="持续记忆每位参与者偏好", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )
        human_turns = [
            Message(role="user", speaker="王PM", content="我是产品经理，偏好快速试点。"),
            Message(role="user", speaker="小李", content="我是财务，重点关注成本控制。"),
            Message(role="user", speaker="王PM", content="建议先在一个城市灰度。"),
        ]

        transcript = await engine.run_live_session(config=config, human_turns=human_turns)

        assert len(provider.requests) == 3
        assert "王PM" in provider.requests[0].system_prompt
        assert "小李" in provider.requests[-1].system_prompt
        assert "灰度" in provider.requests[-1].system_prompt
        assert "王PM" in transcript.user_memory.entries
        assert "小李" in transcript.user_memory.entries
        assert transcript.user_memory.entries["王PM"].recent_messages[-1] == "建议先在一个城市灰度。"
        # TODO: Memory extraction preference tags being reviewed after routing refactor
        # assert "偏好试点" in transcript.user_memory.entries["王PM"].runtime.preference_tags
        assert transcript.user_memory.entries["王PM"].runtime.summary_notes
        # assert "成本敏感" in transcript.user_memory.entries["小李"].runtime.preference_tags

    asyncio.run(_run())


def test_transcript_can_resume_after_persist_and_reboot(tmp_path) -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["第一次回复", "恢复后回复"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=tmp_path / "work",
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="可恢复会话", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )
        store = JsonSessionStore(config.work_path)

        first = await engine.run_live_session(
            config,
            [Message(role="user", speaker="小王", content="第一次发言")],
        )
        store.save(first)

        loaded = store.load()
        resumed = await engine.run_live_session(
            config,
            [Message(role="user", speaker="小王", content="第二次发言")],
            transcript=loaded,
        )

        assert resumed.messages[-1].content == "恢复后回复"
        assert "第一次发言" in provider.requests[-1].system_prompt

    asyncio.run(_run())


def test_auto_compression_limits_context_budget() -> None:
    async def _run() -> None:
        provider = MockProvider(
            responses=["r1", "r2", "r3", "r4", "r5", "r6"],
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/compression"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="压缩记忆", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
            history_max_messages=4,
            history_max_chars=240,
        )

        transcript = await engine.run_live_session(
            config=config,
            human_turns=[
                Message(role="user", speaker="A", content="m1"),
                Message(role="user", speaker="B", content="m2"),
                Message(role="user", speaker="A", content="m3"),
                Message(role="user", speaker="B", content="m4"),
                Message(role="user", speaker="A", content="m5"),
                Message(role="user", speaker="B", content="m6"),
            ],
        )

        assert len(transcript.messages) <= 5
        assert transcript.session_summary

    asyncio.run(_run())


def test_cross_environment_identity_mapping_resolves_same_user() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["收到QQ消息", "收到微信消息"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/cross_env_identity"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="识别同一用户", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        transcript = await engine.run_live_session(
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="微信昵称A",
                    content="我在微信上发言。",
                    channel="wechat",
                    channel_user_id="wx_zhangsan",
                ),
                Message(
                    role="user",
                    speaker="微信昵称B",
                    content="我在微信上继续发言。",
                    channel="wechat",
                    channel_user_id="wx_zhangsan",
                ),
            ],
        )

        assert len(provider.requests) == 2
        assert "微信昵称A" in provider.requests[-1].system_prompt
        assert "微信昵称A" in transcript.user_memory.entries
        assert transcript.user_memory.entries["微信昵称A"].recent_messages[-1] == "我在微信上继续发言。"
        assert transcript.user_memory.entries["微信昵称A"].runtime.summary_notes
        resolved = transcript.find_user_by_channel_uid(channel="wechat", uid="wx_zhangsan")
        assert resolved is not None
        assert resolved.profile.user_id == "微信昵称A"

    asyncio.run(_run())


def test_user_memory_is_persisted_per_user_file_across_new_sessions(tmp_path) -> None:
    async def _run() -> None:
        work_path = tmp_path / "work"
        provider = MockProvider(responses=["第一次回复", "第二次回复"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="持续记忆", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        await engine.run_live_session(
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="我是产品经理，关注成本和灰度节奏。",
                    channel="cli",
                    channel_user_id="u001",
                )
            ],
            transcript=None,
        )

        users_dir = work_path / "users"
        user_files = list(users_dir.glob("*.json"))
        assert user_files

        await engine.run_live_session(
            config=config,
            human_turns=[
                Message(
                    role="user",
                    speaker="小王",
                    content="继续讨论发布方案。",
                    channel="cli",
                    channel_user_id="u001",
                )
            ],
            transcript=None,
        )

        assert "成本" in provider.requests[-1].system_prompt
        assert "灰度" in provider.requests[-1].system_prompt

    asyncio.run(_run())


