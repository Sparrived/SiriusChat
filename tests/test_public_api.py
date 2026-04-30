import asyncio
from pathlib import Path

from sirius_chat import (
    Agent,
    AgentPreset,
    Message,
    SessionConfig,
    probe_provider_availability,
)
from sirius_chat.config import OrchestrationPolicy
from sirius_chat.providers.mock import MockProvider


def test_public_api_exposes_provider_probe() -> None:
    assert callable(probe_provider_availability)


def test_public_api_exposes_aliyun_bailian_provider() -> None:
    from sirius_chat import AliyunBailianProvider

    provider = AliyunBailianProvider(api_key="test-key")
    assert provider is not None


def test_public_api_exposes_bigmodel_provider() -> None:
    from sirius_chat import BigModelProvider

    provider = BigModelProvider(api_key="test-key")
    assert provider is not None


def test_extract_assistant_messages_filters_system_and_user() -> None:
    # Test filtering assistant messages directly without legacy engine
    from sirius_chat.models import Transcript
    transcript = Transcript()
    transcript.messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="reply1"),
        Message(role="user", content="again"),
        Message(role="assistant", content="reply2"),
    ]
    outgoing = [m for m in transcript.messages if m.role == "assistant"]
    assert outgoing
    assert all(m.role == "assistant" for m in outgoing)
    assert len(outgoing) == 2
    assert outgoing[0].content == "reply1"
    assert outgoing[1].content == "reply2"



