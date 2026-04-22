from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RelationshipState:
    def compute_familiarity(self) -> float:
        return 0.5


@dataclass
class GroupSemanticProfile:
    group_id: str = ""
    group_name: str = ""
    typical_interaction_style: str = ""
    interest_topics: list[str] = field(default_factory=list)
    atmosphere_history: list[Any] = field(default_factory=list)


@dataclass
class UserSemanticProfile:
    user_id: str = ""
    communication_style: str = ""


@dataclass
class AtmosphereSnapshot:
    timestamp: str = ""
    group_valence: float = 0.0
    group_arousal: float = 0.0
    active_participants: int = 0


__all__ = [
    "RelationshipState",
    "GroupSemanticProfile",
    "UserSemanticProfile",
    "AtmosphereSnapshot",
]
