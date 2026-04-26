from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RelationshipState:
    trust_score: float = 0.5
    dependency_score: float = 0.5
    emotional_intimacy: float = 0.5
    interaction_frequency_7d: float = 0.0
    first_interaction_at: str = ""
    last_interaction_at: str = ""

    def compute_familiarity(self) -> float:
        return min(1.0, 0.3 + self.interaction_frequency_7d * 0.5 + self.emotional_intimacy * 0.2)


@dataclass
class AtmosphereSnapshot:
    timestamp: str = ""
    group_valence: float = 0.0
    group_arousal: float = 0.0
    active_participants: int = 0


@dataclass
class GroupSemanticProfile:
    group_id: str = ""
    group_name: str = ""
    typical_interaction_style: str = ""
    interest_topics: list[str] = field(default_factory=list)
    atmosphere_history: list[Any] = field(default_factory=list)
    group_norms: dict[str, Any] = field(default_factory=dict)
    taboo_topics: list[str] = field(default_factory=list)
    dominant_topic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "typical_interaction_style": self.typical_interaction_style,
            "interest_topics": list(self.interest_topics),
            "atmosphere_history": [
                {
                    "timestamp": s.timestamp,
                    "group_valence": s.group_valence,
                    "group_arousal": s.group_arousal,
                    "active_participants": s.active_participants,
                }
                for s in self.atmosphere_history
                if isinstance(s, AtmosphereSnapshot)
            ],
            "group_norms": dict(self.group_norms),
            "taboo_topics": list(self.taboo_topics),
            "dominant_topic": self.dominant_topic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GroupSemanticProfile":
        raw_history = data.get("atmosphere_history", [])
        history: list[AtmosphereSnapshot] = []
        for item in raw_history:
            if isinstance(item, dict):
                history.append(AtmosphereSnapshot(**item))
            elif isinstance(item, AtmosphereSnapshot):
                history.append(item)
        return cls(
            group_id=data.get("group_id", ""),
            group_name=data.get("group_name", ""),
            typical_interaction_style=data.get("typical_interaction_style", ""),
            interest_topics=list(data.get("interest_topics", [])),
            atmosphere_history=history,
            group_norms=dict(data.get("group_norms", {})),
            taboo_topics=list(data.get("taboo_topics", [])),
            dominant_topic=data.get("dominant_topic", ""),
        )


@dataclass
class InterestNode:
    topic: str = ""
    participation: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "participation": self.participation}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterestNode":
        return cls(topic=data.get("topic", ""), participation=data.get("participation", 0.0))


@dataclass
class UserSemanticProfile:
    user_id: str = ""
    communication_style: str = ""
    interest_graph: list[Any] = field(default_factory=list)
    relationship_state: RelationshipState = field(default_factory=RelationshipState)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "communication_style": self.communication_style,
            "interest_graph": [
                n.to_dict() if isinstance(n, InterestNode) else dict(n)
                for n in self.interest_graph
            ],
            "relationship_state": {
                "trust_score": self.relationship_state.trust_score,
                "dependency_score": self.relationship_state.dependency_score,
                "emotional_intimacy": self.relationship_state.emotional_intimacy,
                "interaction_frequency_7d": self.relationship_state.interaction_frequency_7d,
                "first_interaction_at": self.relationship_state.first_interaction_at,
                "last_interaction_at": self.relationship_state.last_interaction_at,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserSemanticProfile":
        raw_graph = data.get("interest_graph", [])
        graph: list[InterestNode] = []
        for item in raw_graph:
            if isinstance(item, dict):
                graph.append(InterestNode.from_dict(item))
            elif isinstance(item, InterestNode):
                graph.append(item)
        rs_data = data.get("relationship_state", {})
        rs = RelationshipState(
            trust_score=rs_data.get("trust_score", 0.5),
            dependency_score=rs_data.get("dependency_score", 0.5),
            emotional_intimacy=rs_data.get("emotional_intimacy", 0.5),
            interaction_frequency_7d=rs_data.get("interaction_frequency_7d", 0.0),
            first_interaction_at=rs_data.get("first_interaction_at", ""),
            last_interaction_at=rs_data.get("last_interaction_at", ""),
        )
        return cls(
            user_id=data.get("user_id", ""),
            communication_style=data.get("communication_style", ""),
            interest_graph=graph,
            relationship_state=rs,
        )


__all__ = [
    "RelationshipState",
    "GroupSemanticProfile",
    "UserSemanticProfile",
    "AtmosphereSnapshot",
    "InterestNode",
]
