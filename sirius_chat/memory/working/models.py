"""Working memory data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sirius_chat.models.emotion import EmotionState


@dataclass(slots=True)
class WorkingMemoryEntry:
    """A single entry in the working memory sliding window."""

    entry_id: str
    group_id: str
    user_id: str
    role: str  # "human" | "assistant" | "system"
    content: str
    timestamp: str = ""
    # Importance for truncation decisions
    importance: float = 0.5
    protected: bool = False  # Key info (preference, crisis, agreement) protected from truncation
    # Emotional context
    emotion_state: dict[str, Any] = field(default_factory=dict)
    # Mentioned user ids for context linking
    mentioned_user_ids: list[str] = field(default_factory=list)
    # Source metadata
    channel: str = ""
    channel_user_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "importance": self.importance,
            "protected": self.protected,
            "emotion_state": self.emotion_state,
            "mentioned_user_ids": self.mentioned_user_ids,
            "channel": self.channel,
            "channel_user_id": self.channel_user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkingMemoryEntry":
        return cls(
            entry_id=data.get("entry_id", ""),
            group_id=data.get("group_id", ""),
            user_id=data.get("user_id", ""),
            role=data.get("role", "human"),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", ""),
            importance=data.get("importance", 0.5),
            protected=data.get("protected", False),
            emotion_state=dict(data.get("emotion_state", {})),
            mentioned_user_ids=list(data.get("mentioned_user_ids", [])),
            channel=data.get("channel", ""),
            channel_user_id=data.get("channel_user_id", ""),
        )
