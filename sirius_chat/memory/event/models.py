"""Event memory data models (v2 - observation-based).

v2 redesign: user-scoped observations extracted in batches,
replacing per-message heuristic event clustering.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, MISSING
from typing import Any

# Supported observation categories
OBSERVATION_CATEGORIES = frozenset({
    "preference",    # 偏好：喜好、习惯
    "trait",         # 特质：性格、沟通风格
    "relationship",  # 关系：提到的人/组织
    "experience",    # 经历：工作、教育、生活
    "emotion",       # 情绪：情绪模式、触发点
    "goal",          # 目标：计划、当前项目
    "custom",        # 其他
})


@dataclass(slots=True)
class EventMemoryEntry:
    """User-scoped observation extracted from conversation (v2).

    Each entry represents a meaningful observation about a specific participant,
    extracted via batch LLM analysis rather than per-message heuristics.
    """

    event_id: str
    user_id: str = ""
    category: str = "custom"  # preference|trait|relationship|experience|emotion|goal|custom
    summary: str = ""
    confidence: float = 0.5
    evidence_samples: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    mention_count: int = 0
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict; automatically includes any future fields."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventMemoryEntry":
        """Deserialize from dict; new fields with defaults are handled automatically."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
            elif f.default is not MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not MISSING:  # type: ignore[misc]
                kwargs[f.name] = f.default_factory()  # type: ignore[misc]
        return cls(**kwargs)


# Kept for public API backward compatibility — unused in v2 production code.
@dataclass(slots=True)
class ContextualEventInterpretation:
    """Deprecated: contextual interpretation kept for API compatibility."""

    event_id: str
    event_summary: str
    base_confidence: float = 0.65
    keyword_alignment: float = 0.0
    role_alignment: float = 0.0
    emotion_alignment: float = 0.0
    entity_alignment: float = 0.0
    adjusted_confidence: float = 0.65
    recommended_category: str = "normal"
    interpretation_notes: list[str] = field(default_factory=list)
