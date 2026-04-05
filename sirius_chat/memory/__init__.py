"""Memory management module.

Unified memory system for user profiles, runtime states, and event tracking.
"""

# User memory exports
from sirius_chat.memory.user import (
    MAX_MEMORY_FACTS,
    EVENT_DEDUP_WINDOW_MINUTES,
    MemoryFact,
    UserMemoryEntry,
    UserMemoryFileStore,
    UserMemoryManager,
    UserProfile,
    UserRuntimeState,
)

# Event memory exports
from sirius_chat.memory.event import (
    ContextualEventInterpretation,
    EventMemoryEntry,
    EventMemoryFileStore,
    EventMemoryManager,
)

# Quality assessment exports
from sirius_chat.memory.quality import (
    MemoryQualityMetrics,
    UserBehaviorConsistency,
    MemoryQualityAssessor,
    MemoryForgetEngine,
    MemoryQualityReport,
    analyze_workspace_memories,
    cleanup_workspace_memories,
    apply_decay_to_workspace,
    save_quality_report,
    print_console_report,
)

# Trait taxonomy (originally from sirius_chat.trait_taxonomy)
from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY

__all__ = [
    # Constants
    "MAX_MEMORY_FACTS",
    "EVENT_DEDUP_WINDOW_MINUTES",
    # User memory
    "UserProfile",
    "UserRuntimeState",
    "MemoryFact",
    "UserMemoryEntry",
    "UserMemoryManager",
    "UserMemoryFileStore",
    # Event memory
    "ContextualEventInterpretation",
    "EventMemoryEntry",
    "EventMemoryManager",
    "EventMemoryFileStore",
    # Quality assessment
    "MemoryQualityMetrics",
    "UserBehaviorConsistency",
    "MemoryQualityAssessor",
    "MemoryForgetEngine",
    "MemoryQualityReport",
    "analyze_workspace_memories",
    "cleanup_workspace_memories",
    "apply_decay_to_workspace",
    "save_quality_report",
    "print_console_report",
    # Trait taxonomy
    "TRAIT_TAXONOMY",
]
