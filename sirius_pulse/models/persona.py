"""Persona data models for EmotionalGroupChatEngine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PersonaProfile:
    """Prompt-backed persona metadata used by the runtime."""

    # Identity metadata and the complete prompt written by the user.
    name: str = "小星"
    aliases: list[str] = field(default_factory=list)
    full_system_prompt: str = ""

    # Runtime controls are separate from the persona prompt.
    max_tokens_preference: int = 128
    temperature_preference: float = 0.7
    reply_frequency: str = "moderate"

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    version: str = "1.0"
    created_at: str = ""
    source: str = "manual"  # manual/roleplay_bridge

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "full_system_prompt": self.full_system_prompt,
            "max_tokens_preference": self.max_tokens_preference,
            "temperature_preference": self.temperature_preference,
            "reply_frequency": self.reply_frequency,
            "version": self.version,
            "created_at": self.created_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaProfile":
        return cls(
            name=data.get("name", "小星"),
            aliases=list(data.get("aliases", [])),
            full_system_prompt=data.get("full_system_prompt", ""),
            max_tokens_preference=int(data.get("max_tokens_preference", 128)),
            temperature_preference=float(data.get("temperature_preference", 0.7)),
            reply_frequency=data.get("reply_frequency", "moderate"),
            version=data.get("version", "1.0"),
            created_at=data.get("created_at", ""),
            source=data.get("source", "template"),
        )

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def build_system_prompt(self) -> str:
        """构建发送给 LLM 的角色 prompt。委托 PromptFactory。"""
        from sirius_pulse.core.prompt_factory import PromptFactory

        return PromptFactory.build_persona_prompt(
            name=self.name,
            aliases=self.aliases,
            full_system_prompt=self.full_system_prompt,
        )
