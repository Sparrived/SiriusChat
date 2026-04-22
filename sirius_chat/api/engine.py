from __future__ import annotations

from typing import Any

from sirius_chat.config import SessionConfig
from sirius_chat.config.models import WorkspaceBootstrap
from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.models import Message, Transcript
from sirius_chat.providers.base import AsyncLLMProvider, LLMProvider
from sirius_chat.memory.user.simple import UserProfile
from sirius_chat.workspace.runtime import WorkspaceRuntime


# ── v0.28+ factory ──

def create_emotional_engine(
    work_path,
    *,
    provider: LLMProvider | AsyncLLMProvider | None = None,
    persona=None,
    config: dict[str, Any] | None = None,
) -> EmotionalGroupChatEngine:
    """Create a new EmotionalGroupChatEngine (v0.28+).

    Args:
        work_path: Workspace path for persistence.
        provider: Optional LLM provider for async generation tasks.
        persona: Optional PersonaProfile or string archetype name.
        config: Optional engine configuration dict. See docs/configuration.md
            for supported keys.

    Returns:
        Configured EmotionalGroupChatEngine instance.
    """
    provider_async = provider if provider is None or hasattr(provider, "generate_async") else None
    return EmotionalGroupChatEngine(
        work_path=work_path,
        provider_async=provider_async,
        persona=persona,
        config=config,
    )


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


# ── Utility helpers ──

def find_user_by_channel_uid(
    transcript: Transcript,
    *,
    channel: str,
    uid: str,
) -> UserProfile | None:
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
    "EmotionalGroupChatEngine",
    "create_emotional_engine",
    "open_workspace_runtime",
    "find_user_by_channel_uid",
    "extract_assistant_messages",
    "UserProfile",
]
