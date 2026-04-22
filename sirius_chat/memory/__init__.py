"""Memory management module.

Unified memory system for user profiles, runtime states, and event tracking.
"""

# User memory exports
from sirius_chat.memory.user import (
    MAX_MEMORY_FACTS,
    MAX_OBSERVED_SET_SIZE,
    EVENT_DEDUP_WINDOW_MINUTES,
    MemoryFact,
    UserMemoryEntry,
    UserMemoryFileStore,
    UserMemoryManager,
    UserProfile,
    UserRuntimeState,
    UserManager,  # new v2
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

# Self-memory (diary + glossary) exports
from sirius_chat.memory.self import (
    DiaryEntry,
    GlossaryTerm,
    SelfMemoryState,
    SelfMemoryManager,
    SelfMemoryFileStore,
)

# Trait taxonomy (originally from sirius_chat.trait_taxonomy)
from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY

# New v0.28 modules
from sirius_chat.memory.activation_engine import ActivationEngine, DecaySchedule
from sirius_chat.memory.retrieval_engine import MemoryRetriever
from sirius_chat.memory.working import WorkingMemoryManager
from sirius_chat.memory.episodic import EpisodicMemoryManager
from sirius_chat.memory.semantic import SemanticMemoryManager

__all__ = [
    # Constants
    "MAX_MEMORY_FACTS",
    "MAX_OBSERVED_SET_SIZE",
    "EVENT_DEDUP_WINDOW_MINUTES",
    # User memory
    "UserProfile",
    "UserRuntimeState",
    "MemoryFact",
    "UserMemoryEntry",
    "UserMemoryManager",
    "UserMemoryFileStore",
    "UserManager",  # new v2
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
    # Self-memory
    "DiaryEntry",
    "GlossaryTerm",
    "SelfMemoryState",
    "SelfMemoryManager",
    "SelfMemoryFileStore",
    # Trait taxonomy
    "TRAIT_TAXONOMY",
]
