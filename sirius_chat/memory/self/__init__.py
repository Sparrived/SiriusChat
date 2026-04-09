"""AI self-memory system — diary and glossary subsystems.

The diary subsystem allows the AI to autonomously record experiences, impressions,
and reflections with time-based importance decay (forgetting).

The glossary subsystem maintains a dictionary of terms the AI has encountered
and learned definitions for, building domain knowledge over time.
"""

from sirius_chat.memory.self.models import (
    DiaryEntry,
    GlossaryTerm,
    SelfMemoryState,
)
from sirius_chat.memory.self.manager import SelfMemoryManager
from sirius_chat.memory.self.store import SelfMemoryFileStore

__all__ = [
    "DiaryEntry",
    "GlossaryTerm",
    "SelfMemoryState",
    "SelfMemoryManager",
    "SelfMemoryFileStore",
]
