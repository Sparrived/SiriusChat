from __future__ import annotations

from typing import AsyncIterator, Awaitable, Callable

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import SessionConfig
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.memory import UserMemoryEntry, UserProfile


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
    transcript: Transcript | None = None,
    environment_context: str = "",
    user_profile: UserProfile | None = None,
    on_reply: Callable[[Message], Awaitable[None]] | None = None,
    timeout: float = 0,
) -> Transcript:
    """Async facade for single-message live processing.

    .. versionchanged:: 0.12.0
       Added *user_profile*, *on_reply* and *timeout* parameters.
       When *on_reply* is provided the engine subscribes to the event stream
       internally and calls back for each assistant message — no external
       ``asubscribe`` boilerplate needed.

    .. versionchanged:: 0.9.0
       The ``on_message`` callback has been removed.  Use
       :func:`asubscribe` to receive real-time session events instead.
    """
    return await engine.run_live_message(
        config=config,
        turn=turn,
        transcript=transcript,
        environment_context=environment_context,
        user_profile=user_profile,
        on_reply=on_reply,
        timeout=timeout,
    )


async def asubscribe(
    engine: AsyncRolePlayEngine,
    transcript: Transcript,
    *,
    max_queue_size: int = 256,
) -> AsyncIterator[SessionEvent]:
    """Subscribe to real-time session events.

    Returns an async iterator that yields :class:`SessionEvent` objects
    (new messages, SKILL execution status, processing lifecycle, etc.)
    as they are produced by the engine.

    Example::

        async for event in asubscribe(engine, transcript):
            if event.type == SessionEventType.MESSAGE_ADDED:
                send_to_external(event.message)

    Args:
        engine: The engine instance.
        transcript: The active transcript (session).
        max_queue_size: Maximum buffered events per subscriber.

    Yields:
        SessionEvent instances in chronological order.
    """
    async for event in engine.subscribe(transcript, max_queue_size=max_queue_size):
        yield event


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
    "SessionEvent",
    "SessionEventBus",
    "SessionEventType",
    "create_async_engine",
    "ainit_live_session",
    "arun_live_message",
    "asubscribe",
    "find_user_by_channel_uid",
    "extract_assistant_messages",
]
