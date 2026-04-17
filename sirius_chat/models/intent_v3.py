"""Intent analysis models v3: purpose-driven classification aligned with paper."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SocialIntent(Enum):
    """Purpose-driven intent taxonomy (paper §2.1)."""

    HELP_SEEKING = "help_seeking"
    EMOTIONAL = "emotional"
    SOCIAL = "social"
    SILENT = "silent"


class HelpSubtype(Enum):
    TECH_HELP = "tech_help"
    INFO_QUERY = "info_query"
    DECISION_HELP = "decision_help"


class EmotionalSubtype(Enum):
    VENTING = "venting"
    SEEKING_EMPATHY = "seeking_empathy"
    COMPANIONSHIP = "companionship"
    CELEBRATION = "celebration"


class SocialSubtype(Enum):
    TOPIC_DISCUSSION = "topic_discussion"
    RELATIONSHIP_MAINTENANCE = "relationship_maintenance"
    HUMOR = "humor"


class SilentSubtype(Enum):
    PRIVATE_CHAT = "private_chat"
    FILLER = "filler"
    IRRELEVANT = "irrelevant"


INTENT_SUBTYPE_MAP: dict[SocialIntent, type[Enum]] = {
    SocialIntent.HELP_SEEKING: HelpSubtype,
    SocialIntent.EMOTIONAL: EmotionalSubtype,
    SocialIntent.SOCIAL: SocialSubtype,
    SocialIntent.SILENT: SilentSubtype,
}


@dataclass(slots=True)
class IntentAnalysisV3:
    """Extended intent analysis result compatible with v2 + v3 fields."""

    # === v2 compatible fields ===
    intent_type: str = "chat"
    target: str = "unknown"
    target_scope: str = "unknown"
    directed_at_current_ai: bool = False
    importance: float = 0.5

    # === v3 purpose-driven fields ===
    social_intent: SocialIntent = field(default_factory=lambda: SocialIntent.SOCIAL)
    intent_subtype: str = ""
    urgency_score: float = 0.0  # 0-100
    relevance_score: float = 0.5  # 0-1
    confidence: float = 0.8
    response_priority: int = 5  # 1-10
    estimated_response_time: float = 0.0  # seconds; 0 = immediate

    # === multi-factor decision support ===
    activity_factor: float = 1.0
    relationship_factor: float = 1.0
    time_factor: float = 1.0
    threshold: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_type": self.intent_type,
            "target": self.target,
            "target_scope": self.target_scope,
            "directed_at_current_ai": self.directed_at_current_ai,
            "importance": self.importance,
            "social_intent": self.social_intent.value,
            "intent_subtype": self.intent_subtype,
            "urgency_score": self.urgency_score,
            "relevance_score": self.relevance_score,
            "confidence": self.confidence,
            "response_priority": self.response_priority,
            "estimated_response_time": self.estimated_response_time,
            "activity_factor": self.activity_factor,
            "relationship_factor": self.relationship_factor,
            "time_factor": self.time_factor,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IntentAnalysisV3":
        si_raw = data.get("social_intent", "social")
        try:
            social_intent = SocialIntent(si_raw)
        except ValueError:
            social_intent = SocialIntent.SOCIAL
        return cls(
            intent_type=data.get("intent_type", "chat"),
            target=data.get("target", "unknown"),
            target_scope=data.get("target_scope", "unknown"),
            directed_at_current_ai=data.get("directed_at_current_ai", False),
            importance=data.get("importance", 0.5),
            social_intent=social_intent,
            intent_subtype=data.get("intent_subtype", ""),
            urgency_score=data.get("urgency_score", 0.0),
            relevance_score=data.get("relevance_score", 0.5),
            confidence=data.get("confidence", 0.8),
            response_priority=data.get("response_priority", 5),
            estimated_response_time=data.get("estimated_response_time", 0.0),
            activity_factor=data.get("activity_factor", 1.0),
            relationship_factor=data.get("relationship_factor", 1.0),
            time_factor=data.get("time_factor", 1.0),
            threshold=data.get("threshold", 0.5),
        )
