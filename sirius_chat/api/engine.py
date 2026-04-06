from __future__ import annotations

from typing import Callable

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import SessionConfig
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.memory import UserMemoryEntry

OnMessage = Callable[[Message], None]


def create_async_engine(provider: LLMProvider | AsyncLLMProvider) -> AsyncRolePlayEngine:
    """Create an async roleplay engine for non-blocking integration."""
    return AsyncRolePlayEngine(provider=provider)


async def ainit_live_session(
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    transcript: Transcript | None = None,
) -> Transcript:
    """Async facade for live session initialization."""
    return await engine.run_live_session(
        config=config,
        transcript=transcript,
    )


async def arun_live_message(
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    turn: Message,
    on_message: OnMessage | None = None,
    transcript: Transcript | None = None,
) -> Transcript:
    """Async facade for single-message live processing."""
    return await engine.run_live_message(
        config=config,
        turn=turn,
        on_message=on_message,
        transcript=transcript,
    )


def find_user_by_channel_uid(
    transcript: Transcript,
    *,
    channel: str,
    uid: str,
) -> UserMemoryEntry | None:
    """Stable external lookup by channel + uid."""
    return transcript.find_user_by_channel_uid(channel=channel, uid=uid)


def extract_assistant_messages(
    transcript: Transcript,
    *,
    since_index: int = 0,
) -> list[Message]:
    """Extract assistant messages only.

    Useful for downstream delivery to avoid sending internal system notes.
    """
    start = max(0, int(since_index))
    return [m for m in transcript.messages[start:] if m.role == "assistant"]


__all__ = [
    "AsyncRolePlayEngine",
    "OnMessage",
    "create_async_engine",
    "ainit_live_session",
    "arun_live_message",
    "find_user_by_channel_uid",
    "extract_assistant_messages",
]
