from __future__ import annotations

from typing import AsyncIterator, Awaitable, Callable

from sirius_chat.async_engine import AsyncRolePlayEngine
from sirius_chat.config import SessionConfig
from sirius_chat.config.models import WorkspaceBootstrap
from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.memory import UserMemoryEntry, UserProfile
from sirius_chat.workspace.runtime import WorkspaceRuntime


# ── Legacy factory (kept for reference; prefer create_emotional_engine) ──

def create_async_engine(provider: LLMProvider | AsyncLLMProvider) -> AsyncRolePlayEngine:
    """Create an async roleplay engine for non-blocking integration.

    .. deprecated:: 0.28
       Use :func:`create_emotional_engine` for new projects.
    """
    return AsyncRolePlayEngine(provider)


# ── New v0.28+ factory ──

def create_emotional_engine(
    work_path,
    *,
    provider: LLMProvider | AsyncLLMProvider | None = None,
) -> EmotionalGroupChatEngine:
    """Create a new EmotionalGroupChatEngine (v0.28+).

    Args:
        work_path: Workspace path for persistence.
        provider: Optional LLM provider for async generation tasks.

    Returns:
        Configured EmotionalGroupChatEngine instance.
    """
    return EmotionalGroupChatEngine(work_path=work_path)


# ── Workspace runtime ──

def open_workspace_runtime(
    work_path,
    *,
    config_path=None,
    provider: LLMProvider | AsyncLLMProvider | None = None,
    bootstrap: WorkspaceBootstrap | None = None,
    persist_bootstrap: bool = True,
) -> WorkspaceRuntime:
    """Open a workspace runtime that owns persistence and session recovery.

    When *bootstrap* is provided the host-supplied defaults are merged into the
    workspace on the first ``initialize()`` call.  If *persist_bootstrap* is
    ``True`` (default) the merged values are written to the workspace files so
    that subsequent launches recover them automatically.
    """
    return WorkspaceRuntime.open(
        work_path,
        config_path=config_path,
        provider=provider,
        bootstrap=bootstrap,
        persist_bootstrap=persist_bootstrap,
    )


# ── Legacy session facades (kept for reference) ──

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
    """Async facade for single-message live processing."""
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
    """Subscribe to real-time session events."""
    async for event in engine.subscribe(transcript, max_queue_size=max_queue_size):
        yield event


# ── Utility helpers ──

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
    # Legacy
    "AsyncRolePlayEngine",
    "create_async_engine",
    "ainit_live_session",
    "arun_live_message",
    "asubscribe",
    # v0.28+
    "EmotionalGroupChatEngine",
    "create_emotional_engine",
    # Shared
    "SessionEvent",
    "SessionEventBus",
    "SessionEventType",
    "open_workspace_runtime",
    "find_user_by_channel_uid",
    "extract_assistant_messages",
]
