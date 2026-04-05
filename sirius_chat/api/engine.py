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


async def arun_live_session(
    engine: AsyncRolePlayEngine,
    config: SessionConfig,
    human_turns: list[Message],
    on_message: OnMessage | None = None,
    transcript: Transcript | None = None,
) -> Transcript:
    """Async facade for dynamic live session runs."""
    return await engine.run_live_session(
        config=config,
        human_turns=human_turns,
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


__all__ = [
    "AsyncRolePlayEngine",
    "OnMessage",
    "create_async_engine",
    "arun_live_session",
    "find_user_by_channel_uid",
]
