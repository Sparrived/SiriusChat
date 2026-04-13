"""Data models for AI self-memory system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sirius_chat.mixins import JsonSerializable


@dataclass(slots=True)
class DiaryEntry(JsonSerializable):
    """A single diary entry recorded by the AI.

    The AI autonomously decides what to record. Each entry carries importance
    and keyword tags that drive the forgetting curve.

    Attributes:
        entry_id: Unique identifier (ISO timestamp + hash).
        content: Free-form diary text written by the AI.
        recorded_at: ISO 8601 timestamp of when the entry was created.
        importance: Importance score in [0, 1]. Higher = remembered longer.
        keywords: Tags / keywords for retrieval and relevance matching.
        category: Broad category (reflection | observation | decision | emotion | milestone).
        confidence: Current confidence after decay, starts at 1.0.
        mention_count: How many times this topic was referenced again.
        related_user_ids: Participants related to this entry.
    """

    entry_id: str = ""
    content: str = ""
    recorded_at: str = ""
    importance: float = 0.5
    keywords: list[str] = field(default_factory=list)
    category: str = "observation"
    confidence: float = 1.0
    mention_count: int = 0
    related_user_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.importance = max(0.0, min(1.0, float(self.importance)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if not self.recorded_at:
            self.recorded_at = datetime.now(timezone.utc).isoformat()
        if not self.entry_id:
            import hashlib
            raw = f"{self.recorded_at}:{self.content[:64]}"
            self.entry_id = hashlib.sha256(raw.encode()).hexdigest()[:16]

    def age_days(self) -> float:
        """Days since this entry was recorded."""
        try:
            recorded = datetime.fromisoformat(self.recorded_at)
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - recorded
            return max(0.0, delta.total_seconds() / 86400)
        except (ValueError, TypeError):
            return 0.0


@dataclass(slots=True)
class GlossaryTerm(JsonSerializable):
    """A term/noun definition learned by the AI from conversations.

    The AI collects terms it encounters but does not fully understand,
    then builds definitions from context over time.

    Attributes:
        term: The word or phrase.
        definition: Current best definition.
        source: How the term was learned (conversation | user_explained | inferred).
        first_seen_at: ISO 8601 timestamp of first encounter.
        last_updated_at: ISO 8601 timestamp of last update.
        confidence: How confident the AI is in the definition.
        usage_count: How many times the term appeared in conversations.
        context_examples: Short example sentences showing usage.
        related_terms: Links to related glossary terms.
        domain: Subject area (tech | daily | culture | game | custom).
    """

    term: str = ""
    definition: str = ""
    source: str = "inferred"
    first_seen_at: str = ""
    last_updated_at: str = ""
    confidence: float = 0.5
    usage_count: int = 1
    context_examples: list[str] = field(default_factory=list)
    related_terms: list[str] = field(default_factory=list)
    domain: str = "custom"

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_seen_at:
            self.first_seen_at = now
        if not self.last_updated_at:
            self.last_updated_at = now


@dataclass(slots=True)
class SelfMemoryState:
    """Aggregate state for the AI's self-memory system."""

    diary_entries: list[DiaryEntry] = field(default_factory=list)
    glossary_terms: dict[str, GlossaryTerm] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "diary_entries": [e.to_dict() for e in self.diary_entries],
            "glossary_terms": {k: v.to_dict() for k, v in self.glossary_terms.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> SelfMemoryState:
        entries = [DiaryEntry.from_dict(e) for e in data.get("diary_entries", [])]
        terms = {
            k: GlossaryTerm.from_dict(v)
            for k, v in data.get("glossary_terms", {}).items()
        }
        return cls(diary_entries=entries, glossary_terms=terms)
