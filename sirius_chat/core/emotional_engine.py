"""EmotionalGroupChatEngine: backward-compatible shim.

All implementation has been split into:
  - engine_core   : class definition, __init__, public API, persistence
  - pipeline      : Perception → Cognition → Decision → Execution → BackgroundUpdate
  - bg_tasks      : background tasks, proactive checks, reminders, delayed queue
  - prompt_builders: prompt builders and _generate
  - helpers       : utility methods, token recording, exception classification

This module re-exports the combined class so existing imports continue to work.
"""

from __future__ import annotations

from sirius_chat.core.engine_core import (
    EmotionalGroupChatEngine as _EmotionalGroupChatEngine,
    create_emotional_engine,
)
from sirius_chat.core.pipeline import PipelineMixin
from sirius_chat.core.bg_tasks import BackgroundTasksMixin, _is_reminder_due
from sirius_chat.core.prompt_builders import PromptBuildersMixin
from sirius_chat.core.helpers import HelpersMixin


class EmotionalGroupChatEngine(
    _EmotionalGroupChatEngine,
    PipelineMixin,
    BackgroundTasksMixin,
    PromptBuildersMixin,
    HelpersMixin,
):
    """Combined EmotionalGroupChatEngine with all mixins."""

    pass


# Re-export standalone function and internal helpers for backward compatibility
__all__ = ["EmotionalGroupChatEngine", "create_emotional_engine", "_is_reminder_due"]
