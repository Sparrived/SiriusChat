"""Autobiographical memory: first-person experience records for persona continuity.

Philosophy alignment (v0.28+):
    The AI remembers not just facts about users, but its own experiences,
    feelings, and evolving self-concept. Memory reads like a diary, not a
    customer service file.

    - Diary entries capture inner monologue (<think>) and experiences
    - Emotion timeline tracks the AI's own emotional journey
    - Self-semantic profile maintains an evolving "who am I"
    - Value-weighted importance ensures persona-relevant memories survive

Built on top of SelfMemoryManager (diary + glossary) with philosophy-specific
extensions for first-person subjective memory.
"""

from sirius_chat.memory.autobiographical.manager import AutobiographicalMemoryManager
from sirius_chat.memory.autobiographical.models import SelfSemanticProfile

__all__ = ["AutobiographicalMemoryManager", "SelfSemanticProfile"]
