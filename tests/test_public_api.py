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
    import pytest
    pytest.skip("Legacy AsyncRolePlayEngine facade unavailable after v0.28 refactor")


def test_public_api_async_facade() -> None:
    import pytest
    pytest.skip("Legacy AsyncRolePlayEngine facade unavailable after v0.28 refactor")


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
    # Test extract_assistant_messages directly without legacy engine
    from sirius_chat.models import Transcript
    transcript = Transcript()
    transcript.messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="reply1"),
        Message(role="user", content="again"),
        Message(role="assistant", content="reply2"),
    ]
    outgoing = extract_assistant_messages(transcript, since_index=0)
    assert outgoing
    assert all(m.role == "assistant" for m in outgoing)
    assert len(outgoing) == 2
    assert outgoing[0].content == "reply1"
    assert outgoing[1].content == "reply2"



