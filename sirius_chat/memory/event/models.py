"""Event memory data models"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ContextualEventInterpretation:
    """Contextual understanding of event: adjust event understanding with user history."""

    event_id: str
    event_summary: str
    base_confidence: float = 0.65
    # Consistency scores (0-1): alignment degree between event and user history
    keyword_alignment: float = 0.0  # Overlap between event keywords and user history
    role_alignment: float = 0.0  # Overlap between event roles and user known roles
    emotion_alignment: float = 0.0  # Similarity between event emotions and user history emotions
    entity_alignment: float = 0.0  # Overlap between event entities and user known entities
    # Adjusted confidence
    adjusted_confidence: float = 0.65
    # Recommended handling category
    recommended_category: str = "normal"  # normal|high_confidence|pending|low_relevance
    # Alignment detail notes
    interpretation_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EventMemoryEntry:
    """Event memory entry."""
    
    event_id: str
    summary: str
    keywords: list[str] = field(default_factory=list)
    role_slots: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    time_hints: list[str] = field(default_factory=list)
    emotion_tags: list[str] = field(default_factory=list)
    evidence_samples: list[str] = field(default_factory=list)
    hit_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    verified: bool = False  # Whether this event has been LLM-verified
    mention_count: int = 0  # Number of related mentions accumulated
