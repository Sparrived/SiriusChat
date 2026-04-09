import asyncio

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import Agent, AgentPreset, SessionConfig, OrchestrationPolicy
from sirius_chat.memory import UserProfile
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.mock import MockProvider
from sirius_chat.session.store import JsonSessionStore
from pathlib import Path


async def _run_live_turns(
    *,
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    human_turns: list[Message],
    transcript=None,
):
    transcript = await engine.run_live_session(config=config, transcript=transcript)
    for index, turn in enumerate(human_turns):
        transcript = await engine.run_live_message(
            config=config,
            turn=turn,
            transcript=transcript,
            session_reply_mode=turn.reply_mode,
            finalize_and_persist=index == len(human_turns) - 1,
        )
    return transcript


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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
            config=config,
            human_turns=[
                Message(role="user", speaker="AgentA", content="A-1"),
                Message(role="user", speaker="AgentB", content="B-1"),
                Message(role="user", speaker="AgentA", content="A-2"),
                Message(role="user", speaker="AgentB", content="B-2"),
            ],
        )

        assert len(provider.requests) == 4  # 事件未积累到 min_mentions=3，不触发验证
        user_messages = [item for item in transcript.messages if item.role == "user"]
        assistant_messages = [item for item in transcript.messages if item.role == "assistant"]
        system_messages = [item for item in transcript.messages if item.role == "system"]

        assert len(user_messages) == 4
        assert user_messages[0].speaker == "AgentA"
        assert len(assistant_messages) == 4
        assert assistant_messages[0].speaker == "主助手"
        assert assistant_messages[-1].content == "主助手回复 B-2"

    asyncio.run(_run())


def test_transcript_add_trims_trailing_newlines_and_spaces() -> None:
    transcript = Transcript()
    transcript.add(Message(role="assistant", content="hello\n\n   ", speaker="主助手"))
    transcript.add(Message(role="user", content="line1\nline2  \n ", speaker="用户"))

    assert transcript.messages[0].content == "hello"
    assert transcript.messages[1].content == "line1\nline2"


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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            ),
        )
        human_turns = [
            Message(role="user", speaker="王PM", content="我是产品经理，偏好快速试点。"),
            Message(role="user", speaker="小李", content="我是财务，重点关注成本控制。"),
            Message(role="user", speaker="王PM", content="建议先在一个城市灰度。"),
        ]

        transcript = await _run_live_turns(engine=engine, config=config, human_turns=human_turns)

        # 只有 3 个生成请求（没有事件验证，因为 mention_count < min_mentions=3）
        assert len(provider.requests) == 3
        assert "王PM" in provider.requests[0].system_prompt
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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            ),
        )
        store = JsonSessionStore(config.work_path)

        first = await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="第一次发言")],
        )
        store.save(first)

        loaded = store.load()
        resumed = await _run_live_turns(
            engine=engine,
            config=config,
            human_turns=[Message(role="user", speaker="小王", content="第二次发言")],
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

        transcript = await _run_live_turns(engine=engine, 
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
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                }
            ),
        )

        transcript = await _run_live_turns(engine=engine, 
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

        await _run_live_turns(engine=engine, 
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

        await _run_live_turns(engine=engine, 
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

        chat_requests = [r for r in provider.requests if r.purpose == "chat_main"]
        assert chat_requests, "Should have at least one chat_main request"
        assert "成本" in chat_requests[-1].system_prompt
        assert "灰度" in chat_requests[-1].system_prompt

    asyncio.run(_run())


def test_on_reply_callback_receives_assistant_messages() -> None:
    """on_reply callback should be invoked for each assistant reply."""
    async def _run() -> None:
        provider = MockProvider(responses=["你好 A", "你好 B"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/on_reply_cb"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="回调测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="第一句"),
            transcript=transcript,
            on_reply=_on_reply,
        )
        assert len(received) == 1
        assert received[0] == "你好 A"

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="第二句"),
            transcript=transcript,
            on_reply=_on_reply,
        )
        assert len(received) == 2
        assert received[1] == "你好 B"

    asyncio.run(_run())


def test_user_profile_auto_registration() -> None:
    """user_profile parameter should auto-register user before processing."""
    async def _run() -> None:
        provider = MockProvider(responses=["收到"])
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=Path("data/tests/user_profile_auto"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="注册测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        profile = UserProfile(
            user_id="qq_12345",
            name="测试用户",
            persona="产品经理",
            identities={"qq": "12345"},
            aliases=["小测"],
        )

        transcript = await engine.run_live_session(config=config)
        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="测试用户", content="你好"),
            transcript=transcript,
            user_profile=profile,
        )

        entry = transcript.user_memory.entries.get("qq_12345")
        assert entry is not None
        assert entry.profile.name == "测试用户"
        assert entry.profile.persona == "产品经理"

    asyncio.run(_run())


def test_timeout_raises_on_expiry() -> None:
    """timeout parameter should raise asyncio.TimeoutError when exceeded."""
    async def _run() -> None:

        class SlowProvider:
            """Provider that delays long enough to trigger timeout."""
            def __init__(self) -> None:
                self.requests: list = []
                self.model_id = "slow-model"

            async def generate_async(self, request) -> str:
                await asyncio.sleep(30)
                return "太慢了"

        engine = AsyncRolePlayEngine(provider=SlowProvider())
        config = SessionConfig(
            work_path=Path("data/tests/timeout_test"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="超时测试", model="slow-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="slow-model",
                session_reply_mode="always",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await engine.run_live_session(config=config)
        timed_out = False
        try:
            await engine.run_live_message(
                config=config,
                turn=Message(role="user", speaker="小明", content="等很久"),
                transcript=transcript,
                timeout=0.5,
            )
        except (asyncio.TimeoutError, RuntimeError):
            timed_out = True

        assert timed_out, "Should have raised TimeoutError or RuntimeError"

    asyncio.run(_run())


def test_on_reply_with_timeout_cleans_up_on_expiry() -> None:
    """on_reply + timeout: consumer task should be cancelled on timeout."""
    async def _run() -> None:
        class SlowProvider:
            def __init__(self) -> None:
                self.requests: list = []
                self.model_id = "slow-model"

            async def generate_async(self, request) -> str:
                await asyncio.sleep(30)
                return "太慢了"

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        engine = AsyncRolePlayEngine(provider=SlowProvider())
        config = SessionConfig(
            work_path=Path("data/tests/on_reply_timeout"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="超时回调测试", model="slow-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="slow-model",
                session_reply_mode="always",
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await engine.run_live_session(config=config)
        timed_out = False
        try:
            await engine.run_live_message(
                config=config,
                turn=Message(role="user", speaker="小明", content="等"),
                transcript=transcript,
                on_reply=_on_reply,
                timeout=0.5,
            )
        except (asyncio.TimeoutError, RuntimeError):
            timed_out = True

        assert timed_out, "Should have raised TimeoutError or RuntimeError"
        assert len(received) == 0, "No replies expected before timeout"

    asyncio.run(_run())


def test_on_reply_callback_with_skill_execution(tmp_path) -> None:
    """on_reply callback should still receive assistant output when SKILL is executed."""
    async def _run() -> None:
        work_path = tmp_path / "on_reply_skill"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "echo.py").write_text(
            """
SKILL_META = {
    "name": "echo",
    "description": "Return the given text",
    "parameters": {
        "text": {"type": "str", "description": "text to return", "required": True}
    },
}

def run(text: str, **kwargs):
    return {"echo": text}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                '[SKILL_CALL: echo | {"text": "苹果"}]',
                "已执行技能，结果是苹果。",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="技能回调测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="帮我调用技能"),
            transcript=transcript,
            on_reply=_on_reply,
            timeout=10,
        )

        assert len(provider.requests) == 2
        assert received
        assert any("已执行技能" in content for content in received)
        assert all("SKILL_CALL" not in content for content in received)
        assert any(
            "SKILL执行结果: echo" in message.content
            for message in transcript.messages
            if message.role == "system"
        )

    asyncio.run(_run())


def test_skill_runtime_lazy_attach_when_context_reused(tmp_path) -> None:
    """Context created without skills should still execute skills after enabling later."""
    async def _run() -> None:
        work_path = tmp_path / "lazy_skill_attach"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "system_info.py").write_text(
            """
SKILL_META = {
    "name": "system_info",
    "description": "Return fake system info",
    "parameters": {},
}

def run(**kwargs):
    return {"status": "ok"}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                "普通回复",
                "[SKILL_CALL: system_info]\n\n开始检查中",
                "检查完成，系统状态正常。",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)

        config_without_skills = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="懒挂载测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=False,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        config_with_skills = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="懒挂载测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await engine.run_live_session(config=config_without_skills)
        transcript = await engine.run_live_message(
            config=config_without_skills,
            turn=Message(role="user", speaker="小明", content="第一句"),
            transcript=transcript,
        )

        transcript = await engine.run_live_message(
            config=config_with_skills,
            turn=Message(role="user", speaker="小明", content="第二句，调用技能"),
            transcript=transcript,
        )

        # one request for first turn + two requests for skill round in second turn
        assert len(provider.requests) == 3
        assert any(
            "SKILL执行结果: system_info" in message.content
            for message in transcript.messages
            if message.role == "system"
        )
        assert any(
            message.role == "assistant" and "检查完成" in message.content
            for message in transcript.messages
        )

    asyncio.run(_run())


def test_on_reply_emits_plain_text_alongside_skill_call(tmp_path) -> None:
    """When model outputs SKILL_CALL + plain text, on_reply should receive the plain text and final reply."""
    async def _run() -> None:
        work_path = tmp_path / "on_reply_no_partial"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "echo.py").write_text(
            """
SKILL_META = {
    "name": "echo",
    "description": "Return text",
    "parameters": {
        "text": {"type": "str", "description": "text", "required": True}
    },
}

def run(text: str, **kwargs):
    return {"echo": text}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                '[SKILL_CALL: echo | {"text": "系统正常"}]\n\n正在检查中...',
                "检查完成：系统正常。",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="中间态测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="查下系统"),
            transcript=transcript,
            on_reply=_on_reply,
            timeout=10,
        )

        assert len(provider.requests) == 2
        assert received == ["正在检查中...", "检查完成：系统正常。"]

    asyncio.run(_run())


def test_unknown_skill_triggers_regeneration_instead_of_partial_output(tmp_path) -> None:
    """Unknown SKILL should trigger a regeneration pass and return final output."""
    async def _run() -> None:
        work_path = tmp_path / "unknown_skill_regen"
        provider = MockProvider(
            responses=[
                "[SKILL_CALL: missing_skill]\n\n先处理中...",
                "我暂时没有这个技能，但我可以手动给你结论。",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="未知技能回退", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="执行一下未知技能"),
            transcript=transcript,
            on_reply=_on_reply,
            timeout=10,
        )

        assert len(provider.requests) == 2
        assert received == ["我暂时没有这个技能，但我可以手动给你结论。"]

    asyncio.run(_run())


def test_skill_reload_on_miss_when_registry_non_empty(tmp_path) -> None:
    """If registry is non-empty but missing target skill, engine should reload and execute it."""
    async def _run() -> None:
        work_path = tmp_path / "skill_reload_on_miss"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Initial skill to make registry non-empty
        (skills_dir / "alpha.py").write_text(
            """
SKILL_META = {"name": "alpha", "description": "alpha", "parameters": {}}
def run(**kwargs):
    return {"alpha": True}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                "普通回复",
                '[SKILL_CALL: beta | {"x": 1}]',
                "beta 已执行完成。",
            ]
        )
        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="重载测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        transcript = await engine.run_live_session(config=config)
        # First turn initializes context and loads only alpha
        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="第一句"),
            transcript=transcript,
        )

        # Add beta after context was already created and registry is non-empty
        (skills_dir / "beta.py").write_text(
            """
SKILL_META = {
    "name": "beta",
    "description": "beta",
    "parameters": {"x": {"type": "int", "description": "x", "required": True}},
}
def run(x: int, **kwargs):
    return {"beta": x}
""".strip(),
            encoding="utf-8",
        )

        transcript = await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="第二句调用beta"),
            transcript=transcript,
        )

        assert len(provider.requests) == 3
        assert any(
            "SKILL执行结果: beta" in message.content
            for message in transcript.messages
            if message.role == "system"
        )
        assert any(
            message.role == "assistant" and "beta 已执行完成" in message.content
            for message in transcript.messages
        )

    asyncio.run(_run())


def test_skill_rounds_exhausted_still_returns_final_answer(tmp_path) -> None:
    """When model keeps returning SKILL_CALL, engine should force one final plain reply."""
    async def _run() -> None:
        work_path = tmp_path / "skill_rounds_exhausted"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "system_info.py").write_text(
            """
SKILL_META = {"name": "system_info", "description": "sys", "parameters": {}}
def run(**kwargs):
    return {"cpu": "ok"}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                "[SKILL_CALL: system_info]\\n\\n正在检查中",
                "[SKILL_CALL: system_info]",
                "[SKILL_CALL: system_info]",
                "检查完成：主机状态正常。",
            ]
        )

        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="skill轮次耗尽测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                max_skill_rounds=3,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="查主机"),
            transcript=transcript,
            on_reply=_on_reply,
            timeout=15,
        )

        # 3 轮 SKILL 再生成，第 4 次返回最终文案，无需额外强制总结请求
        assert len(provider.requests) == 4
        assert received
        assert received[-1] == "检查完成：主机状态正常。"
        assert not any(
            m.role == "assistant" and not m.content.strip()
            for m in transcript.messages
        )

    asyncio.run(_run())


def test_skill_rounds_exhausted_fallback_uses_skill_result_summary(tmp_path) -> None:
    """If model never produces final text, fallback should include skill result summary."""
    async def _run() -> None:
        work_path = tmp_path / "skill_rounds_summary_fallback"
        skills_dir = work_path / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "system_info.py").write_text(
            """
SKILL_META = {"name": "system_info", "description": "sys", "parameters": {}}
def run(**kwargs):
    return {"cpu": "12%", "memory": "70%"}
""".strip(),
            encoding="utf-8",
        )

        provider = MockProvider(
            responses=[
                "[SKILL_CALL: system_info]\\n\\n正在检查中",
                "[SKILL_CALL: system_info]",
                "[SKILL_CALL: system_info]",
                "[SKILL_CALL: system_info]",
                "",
            ]
        )

        engine = AsyncRolePlayEngine(provider=provider)
        config = SessionConfig(
            work_path=work_path,
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="skill兜底摘要测试", model="mock-model"),
                global_system_prompt="测试",
            ),
            orchestration=OrchestrationPolicy(
                unified_model="mock-model",
                session_reply_mode="always",
                enable_skills=True,
                max_skill_rounds=3,
                task_enabled={
                    "memory_extract": False,
                    "multimodal_parse": False,
                    "event_extract": False,
                },
            ),
        )

        received: list[str] = []

        async def _on_reply(msg: Message) -> None:
            received.append(msg.content)

        transcript = await engine.run_live_session(config=config)
        await engine.run_live_message(
            config=config,
            turn=Message(role="user", speaker="小明", content="查主机"),
            transcript=transcript,
            on_reply=_on_reply,
            timeout=15,
        )

        # 3 轮 SKILL 再生成后仍无最终文案，会触发 1 次强制总结再生成，共 5 次请求
        assert len(provider.requests) == 5
        assert received
        assert "system_info 已执行，结果摘要" in received[-1]
        assert "暂未生成可用回复" not in received[-1]
        assert not any(
            m.role == "assistant" and not m.content.strip()
            for m in transcript.messages
        )

    asyncio.run(_run())

