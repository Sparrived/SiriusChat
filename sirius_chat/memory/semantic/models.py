"""Semantic memory data models: user profiles + relationship networks + group norms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class InterestNode:
    """Topic-participation-depth triple (paper §5.1.2)."""

    topic: str
    participation: float = 0.0  # 0~1
    depth: float = 0.0          # 0~1 (surface=0, deep=1)
    first_seen_at: str = ""
    last_seen_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "participation": self.participation,
            "depth": self.depth,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterestNode":
        return cls(
            topic=data.get("topic", ""),
            participation=data.get("participation", 0.0),
            depth=data.get("depth", 0.0),
            first_seen_at=data.get("first_seen_at", ""),
            last_seen_at=data.get("last_seen_at", ""),
        )


@dataclass(slots=True)
class RelationshipState:
    """Bilateral relationship metrics (paper §5.1.3)."""

    interaction_frequency_7d: float = 0.0   # Messages per day (last 7 days)
    emotional_intimacy: float = 0.0         # 0~1
    trust_score: float = 0.0                # 0~1 (based on self-disclosure depth)
    dependency_score: float = 0.0           # 0~1 (how often user seeks help first)
    familiarity: float = 0.0                # 0~1 (composite)
    first_interaction_at: str = ""
    last_interaction_at: str = ""
    milestones: list[dict[str, Any]] = field(default_factory=list)

    def compute_familiarity(self) -> float:
        """Compute composite familiarity score."""
        self.familiarity = (
            min(1.0, self.interaction_frequency_7d / 10.0) * 0.3
            + self.emotional_intimacy * 0.3
            + self.trust_score * 0.2
            + self.dependency_score * 0.2
        )
        return self.familiarity

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_frequency_7d": self.interaction_frequency_7d,
            "emotional_intimacy": self.emotional_intimacy,
            "trust_score": self.trust_score,
            "dependency_score": self.dependency_score,
            "familiarity": self.familiarity,
            "first_interaction_at": self.first_interaction_at,
            "last_interaction_at": self.last_interaction_at,
            "milestones": self.milestones,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationshipState":
        return cls(
            interaction_frequency_7d=data.get("interaction_frequency_7d", 0.0),
            emotional_intimacy=data.get("emotional_intimacy", 0.0),
            trust_score=data.get("trust_score", 0.0),
            dependency_score=data.get("dependency_score", 0.0),
            familiarity=data.get("familiarity", 0.0),
            first_interaction_at=data.get("first_interaction_at", ""),
            last_interaction_at=data.get("last_interaction_at", ""),
            milestones=list(data.get("milestones", [])),
        )


@dataclass(slots=True)
class UserSemanticProfile:
    """Semantic user profile extracted from episodic memories (paper §5.1)."""

    user_id: str
    base_attributes: dict[str, Any] = field(default_factory=dict)
    interest_graph: list[InterestNode] = field(default_factory=list)
    relationship_state: RelationshipState = field(default_factory=RelationshipState)
    taboo_boundaries: list[str] = field(default_factory=list)
    important_dates: list[dict[str, str]] = field(default_factory=list)
    communication_style: str = ""  # concise / detailed / formal / casual
    confirmed: bool = False
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "base_attributes": self.base_attributes,
            "interest_graph": [n.to_dict() for n in self.interest_graph],
            "relationship_state": self.relationship_state.to_dict(),
            "taboo_boundaries": self.taboo_boundaries,
            "important_dates": self.important_dates,
            "communication_style": self.communication_style,
            "confirmed": self.confirmed,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserSemanticProfile":
        rel_data = data.get("relationship_state", {})
        rel = (
            RelationshipState.from_dict(rel_data)
            if isinstance(rel_data, dict)
            else RelationshipState()
        )
        return cls(
            user_id=data.get("user_id", ""),
            base_attributes=dict(data.get("base_attributes", {})),
            interest_graph=[
                InterestNode.from_dict(n)
                for n in data.get("interest_graph", [])
                if isinstance(n, dict)
            ],
            relationship_state=rel,
            taboo_boundaries=list(data.get("taboo_boundaries", [])),
            important_dates=list(data.get("important_dates", [])),
            communication_style=data.get("communication_style", ""),
            confirmed=data.get("confirmed", False),
            updated_at=data.get("updated_at", ""),
        )


@dataclass(slots=True)
class AtmosphereSnapshot:
    """Group atmosphere at a specific point in time."""

    timestamp: str
    group_valence: float = 0.0
    group_arousal: float = 0.3
    heat_level: str = "warm"  # cold | warm | hot | overheated
    active_participants: int = 0
    dominant_topic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "group_valence": self.group_valence,
            "group_arousal": self.group_arousal,
            "heat_level": self.heat_level,
            "active_participants": self.active_participants,
            "dominant_topic": self.dominant_topic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AtmosphereSnapshot":
        return cls(
            timestamp=data.get("timestamp", ""),
            group_valence=data.get("group_valence", 0.0),
            group_arousal=data.get("group_arousal", 0.3),
            heat_level=data.get("heat_level", "warm"),
            active_participants=data.get("active_participants", 0),
            dominant_topic=data.get("dominant_topic", ""),
        )


@dataclass(slots=True)
class GroupSemanticProfile:
    """Semantic group profile: norms, culture, atmosphere history (paper §4.4 / §5.2.3)."""

    group_id: str
    group_name: str = ""
    atmosphere_history: list[AtmosphereSnapshot] = field(default_factory=list)
    group_norms: dict[str, Any] = field(default_factory=dict)
    interest_topics: list[str] = field(default_factory=list)
    typical_interaction_style: str = "balanced"  # active | lurker | controversial | balanced
    ai_intervention_feedback: list[dict[str, Any]] = field(default_factory=list)
    taboo_topics: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "atmosphere_history": [s.to_dict() for s in self.atmosphere_history],
            "group_norms": self.group_norms,
            "interest_topics": self.interest_topics,
            "typical_interaction_style": self.typical_interaction_style,
            "ai_intervention_feedback": self.ai_intervention_feedback,
            "taboo_topics": self.taboo_topics,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupSemanticProfile":
        return cls(
            group_id=data.get("group_id", ""),
            group_name=data.get("group_name", ""),
            atmosphere_history=[
                AtmosphereSnapshot.from_dict(s)
                for s in data.get("atmosphere_history", [])
                if isinstance(s, dict)
            ],
            group_norms=dict(data.get("group_norms", {})),
            interest_topics=list(data.get("interest_topics", [])),
            typical_interaction_style=data.get("typical_interaction_style", "balanced"),
            ai_intervention_feedback=list(data.get("ai_intervention_feedback", [])),
            taboo_topics=list(data.get("taboo_topics", [])),
            updated_at=data.get("updated_at", ""),
        )
