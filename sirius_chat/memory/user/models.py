"""User memory data models"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class UserProfile:
    """Initial user profile: provided by external system before session starts.
    
    Should not be arbitrarily overwritten by AI during runtime.
    """

    user_id: str
    name: str
    persona: str = ""
    identities: dict[str, str] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MemoryFact:
    """Traceable memory fact record. Supports multi-model collaboration and conflict detection.
    
    Rich context support:
    - observed_at: ISO 8601 timestamp for precise moment
    - observed_time_desc: Human-friendly time description (e.g. "昨天下午", "上周一")
    - context_channel: Where the information came from (e.g. "qq", "wechat", "cli")
    - context_topic: Conversation topic or domain (e.g. "work", "travel", "hobby")
    
    Confidence tiers:
    - confidence > 0.85: High confidence, persistent storage
    - confidence <= 0.85: Low confidence, transient (cleaned after session)
    """

    fact_type: str
    value: str
    source: str = "unknown"
    confidence: float = 0.5
    observed_at: str = ""
    observed_time_desc: str = ""  # Human-friendly time description
    memory_category: str = "custom"  # identity|preference|emotion|event|custom
    validated: bool = False  # Whether verified by memory_manager
    conflict_with: list[str] = field(default_factory=list)  # List of conflicting memory IDs
    # C2: RESIDENT vs TRANSIENT separation marker
    is_transient: bool = False  # Whether this is a transient fact (confidence ≤ 0.85)
    created_at: str = ""  # Creation time (ISO format), for expiry judgment
    # Rich context fields
    context_channel: str = ""  # Source channel (qq, wechat, cli, etc.)
    context_topic: str = ""  # Conversation topic or domain
    context_metadata: dict[str, str] = field(default_factory=dict)  # Additional context


@dataclass(slots=True)
class UserRuntimeState:
    """Runtime state: continuously updated by system/AI during session."""

    inferred_persona: str = ""
    inferred_traits: list[str] = field(default_factory=list)
    preference_tags: list[str] = field(default_factory=list)
    recent_messages: list[str] = field(default_factory=list)
    summary_notes: list[str] = field(default_factory=list)
    memory_facts: list[MemoryFact] = field(default_factory=list)
    last_seen_channel: str = ""
    last_seen_uid: str = ""
    # Event observation feature set (for consistency comparison with new events)
    observed_keywords: set[str] = field(default_factory=set)
    observed_roles: set[str] = field(default_factory=set)
    observed_emotions: set[str] = field(default_factory=set)
    observed_entities: set[str] = field(default_factory=set)
    # A1: Time window deduplication - record last event processing time
    last_event_processed_at: datetime | None = None


@dataclass(slots=True)
class UserMemoryEntry:
    """User memory entry combining profile and runtime state."""
    
    profile: UserProfile
    runtime: UserRuntimeState = field(default_factory=UserRuntimeState)

    @property
    def recent_messages(self) -> list[str]:
        """Backward-compatible alias for legacy callers."""
        return self.runtime.recent_messages

    @property
    def summary_notes(self) -> list[str]:
        """Backward-compatible alias for legacy callers."""
        return self.runtime.summary_notes
