"""Data models for autobiographical memory layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sirius_chat.mixins import JsonSerializable


@dataclass(slots=True)
class SelfSemanticProfile(JsonSerializable):
    """The AI's evolving self-concept: who it believes it is.

    Unlike the persona (which is a character brief written by the user),
    the self-semantic profile is the AI's own accumulated understanding
    of itself based on its experiences.

    Attributes:
        self_description: Free-form "I am..." text written/refined by the AI.
        core_values: Values inherited from persona, weighted by lived experience.
        value_weights: How strongly each value has been reinforced (0-1).
        emotion_timeline: Recent emotional states with timestamps.
        relationship_self_views: For each user_id, how the AI sees that relationship.
        accumulated_experiences: Count of significant experiences per category.
    """

    self_description: str = ""
    core_values: list[str] = field(default_factory=list)
    value_weights: dict[str, float] = field(default_factory=dict)
    emotion_timeline: list[dict[str, Any]] = field(default_factory=list)
    relationship_self_views: dict[str, str] = field(default_factory=dict)
    accumulated_experiences: dict[str, int] = field(default_factory=dict)
    growth_notes: str = ""  # 自我反思摘要，由后台反思任务更新
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def record_emotion(self, valence: float, arousal: float, trigger: str = "") -> None:
        """Append an emotional snapshot to the timeline."""
        self.emotion_timeline.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "valence": round(valence, 2),
            "arousal": round(arousal, 2),
            "trigger": trigger,
        })
        # Keep bounded
        if len(self.emotion_timeline) > 500:
            self.emotion_timeline = self.emotion_timeline[-500:]
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def reinforce_value(self, value: str, delta: float = 0.05) -> None:
        """Reinforce a core value based on lived experience."""
        current = self.value_weights.get(value, 0.0)
        self.value_weights[value] = min(1.0, current + delta)
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def get_recent_emotion_window(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the last n emotional snapshots."""
        return self.emotion_timeline[-n:]

    def update_growth_notes(self, reflection: str) -> None:
        """Update growth_notes with a new reflection summary."""
        self.growth_notes = reflection
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "self_description": self.self_description,
            "core_values": self.core_values,
            "value_weights": self.value_weights,
            "emotion_timeline": self.emotion_timeline,
            "relationship_self_views": self.relationship_self_views,
            "accumulated_experiences": self.accumulated_experiences,
            "growth_notes": self.growth_notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelfSemanticProfile":
        return cls(
            self_description=data.get("self_description", ""),
            core_values=data.get("core_values", []),
            value_weights=data.get("value_weights", {}),
            emotion_timeline=data.get("emotion_timeline", []),
            relationship_self_views=data.get("relationship_self_views", {}),
            accumulated_experiences=data.get("accumulated_experiences", {}),
            growth_notes=data.get("growth_notes", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
