import asyncio
from pathlib import Path

from sirius_chat.api import (
    Agent,
    AgentPreset,
    Message,
    SessionConfig,
    ainit_live_session,
    arun_live_message,
    create_async_engine,
    extract_assistant_messages,
    find_user_by_channel_uid,
    probe_provider_availability,
)
from sirius_chat.config import OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider


def test_public_api_live_session_and_identity_lookup() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["ok"])
        engine = create_async_engine(provider)

        config = SessionConfig(
            work_path=Path("data/tests/public_api"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        transcript = await ainit_live_session(
            engine=engine,
            config=config,
        )
        transcript = await arun_live_message(
            engine=engine,
            config=config,
            transcript=transcript,
            turn=Message(
                role="user",
                speaker="微信昵称",
                content="hello",
                channel="wechat",
                channel_user_id="wx_1",
            ),
        )

        entry = find_user_by_channel_uid(transcript, channel="wechat", uid="wx_1")
        assert entry is not None
        assert entry.profile.user_id == "微信昵称"

    asyncio.run(_run())


def test_public_api_async_facade() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["ok-async"])
        engine = create_async_engine(provider)

        config = SessionConfig(
            work_path=Path("data/tests/public_api_async"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="test", model="mock-model"),
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

        transcript = await ainit_live_session(
            engine=engine,
            config=config,
        )
        transcript = await arun_live_message(
            engine=engine,
            config=config,
            transcript=transcript,
            turn=Message(role="user", speaker="小王", content="hello"),
        )
        assert transcript.messages[-1].content == "ok-async"

    asyncio.run(_run())


def test_public_api_exposes_provider_probe() -> None:
    assert callable(probe_provider_availability)


def test_public_api_exposes_aliyun_bailian_provider() -> None:
    from sirius_chat.api import AliyunBailianProvider

    provider = AliyunBailianProvider(api_key="test-key")
    assert provider is not None


def test_public_api_exposes_bigmodel_provider() -> None:
    from sirius_chat.api import BigModelProvider

    provider = BigModelProvider(api_key="test-key")
    assert provider is not None


def test_extract_assistant_messages_filters_system_and_user() -> None:
    async def _run() -> None:
        provider = MockProvider(responses=["ok-1", "ok-2"])
        engine = create_async_engine(provider)

        config = SessionConfig(
            work_path=Path("data/tests/public_api_extract_assistant"),
            preset=AgentPreset(
                agent=Agent(name="主助手", persona="test", model="mock-model"),
                global_system_prompt="测试系统提示词",
            ),
        )

        transcript = await ainit_live_session(engine=engine, config=config)
        start_index = len(transcript.messages)
        transcript = await arun_live_message(
            engine=engine,
            config=config,
            transcript=transcript,
            turn=Message(role="user", speaker="小王", content="hello"),
        )

        outgoing = extract_assistant_messages(transcript, since_index=start_index)
        assert outgoing
        assert all(m.role == "assistant" for m in outgoing)

    asyncio.run(_run())



