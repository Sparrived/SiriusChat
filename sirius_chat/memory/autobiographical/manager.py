"""Autobiographical memory manager: first-person experience records."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sirius_chat.memory.self.manager import SelfMemoryManager
from sirius_chat.memory.self.models import DiaryEntry
from sirius_chat.memory.self.store import SelfMemoryFileStore
from sirius_chat.memory.autobiographical.models import SelfSemanticProfile
from sirius_chat.models.emotion import EmotionState
from sirius_chat.models.persona import PersonaProfile
from sirius_chat.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)

_MAX_THOUGHTS = 200


class AutobiographicalMemoryManager:
    """Manages the AI's autobiographical memory: diary, self-concept, emotion timeline.

    Wraps SelfMemoryManager (diary + glossary) and adds:
    - First-person experience recording from <think> tags
    - Emotion timeline tracking
    - Self-semantic profile evolution
    - Value-weighted importance scoring (zero LLM cost)
    """

    def __init__(
        self,
        work_path: str | Path | WorkspaceLayout,
        persona: PersonaProfile | None = None,
    ) -> None:
        self._layout = work_path if isinstance(work_path, WorkspaceLayout) else WorkspaceLayout(Path(work_path))
        self.persona = persona

        # Load or create underlying self-memory (diary + glossary)
        self._file_store = SelfMemoryFileStore(self._layout)
        self._self_memory = self._file_store.load()

        # Self-semantic profile (who am I)
        self._profile = SelfSemanticProfile()
        self._profile.core_values = list(persona.core_values) if persona else []
        self._profile.self_description = (
            f"我是{persona.name}。" if persona and persona.name else ""
        )

        # Recent thoughts buffer (before they become diary entries)
        self._recent_thoughts: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_thought(
        self,
        content: str,
        emotion: EmotionState | None = None,
        trigger_message: str = "",
        group_id: str | None = None,
    ) -> DiaryEntry | None:
        """Record an inner monologue (<think>) as a diary entry.

        Returns the created DiaryEntry, or None if content is empty.
        """
        if not content or not content.strip():
            return None

        importance = self._compute_importance(content, emotion)
        entry = DiaryEntry(
            content=content.strip(),
            importance=importance,
            keywords=self._extract_keywords(content),
            category="reflection",
            confidence=1.0,
            related_user_ids=[],
        )

        # Attach emotion snapshot as extended metadata (not in base DiaryEntry)
        # Store in the buffer for now; full integration in v0.28+
        self._recent_thoughts.append({
            "entry_id": entry.entry_id,
            "timestamp": entry.recorded_at,
            "content": entry.content,
            "importance": entry.importance,
            "emotion": emotion.to_dict() if emotion else {},
            "trigger": trigger_message,
            "group_id": group_id,
        })
        if len(self._recent_thoughts) > _MAX_THOUGHTS:
            self._recent_thoughts = self._recent_thoughts[-_MAX_THOUGHTS:]

        # Also add to the underlying diary system
        self._self_memory.add_diary_entry(entry)

        # Update emotion timeline
        if emotion:
            self._profile.record_emotion(
                emotion.valence, emotion.arousal, trigger=trigger_message[:50]
            )

        # Reinforce values mentioned in the thought
        self._reinforce_values_from_content(content)

        return entry

    def record_experience(
        self,
        content: str,
        emotion: EmotionState | None = None,
        category: str = "observation",
        user_ids: list[str] | None = None,
    ) -> DiaryEntry | None:
        """Record a general first-person experience."""
        if not content or not content.strip():
            return None

        importance = self._compute_importance(content, emotion)
        entry = DiaryEntry(
            content=content.strip(),
            importance=importance,
            keywords=self._extract_keywords(content),
            category=category,
            confidence=1.0,
            related_user_ids=user_ids or [],
        )
        self._self_memory.add_diary_entry(entry)

        if emotion:
            self._profile.record_emotion(
                emotion.valence, emotion.arousal, trigger=content[:50]
            )

        cat = category
        self._profile.accumulated_experiences[cat] = (
            self._profile.accumulated_experiences.get(cat, 0) + 1
        )

        return entry

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_recent_thoughts(self, n: int = 10) -> list[dict[str, Any]]:
        """Return recent thoughts from the buffer."""
        return self._recent_thoughts[-n:]

    def get_emotion_timeline(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the AI's recent emotional journey."""
        return self._profile.get_recent_emotion_window(n)

    def get_relevant_diary_entries(
        self,
        keywords: list[str] | None = None,
        max_entries: int = 8,
    ) -> list[DiaryEntry]:
        """Retrieve diary entries most relevant to current context."""
        return self._self_memory.get_relevant_diary_entries(keywords, max_entries)

    def build_diary_prompt_section(
        self,
        keywords: list[str] | None = None,
    ) -> str:
        """Build a compact diary section for injection into LLM prompts."""
        return self._self_memory.build_diary_prompt_section(keywords)

    def build_self_prompt_section(self) -> str:
        """Build a self-concept section for injection into LLM prompts."""
        lines: list[str] = ["[我是谁]"]
        if self._profile.self_description:
            lines.append(self._profile.self_description)
        if self._profile.core_values:
            values = ", ".join(self._profile.core_values[:5])
            lines.append(f"我在乎的：{values}")
        recent_emotions = self._profile.get_recent_emotion_window(3)
        if recent_emotions:
            latest = recent_emotions[-1]
            lines.append(
                f"最近的心情：愉悦度{latest['valence']}，紧张度{latest['arousal']}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Importance & value scoring (zero LLM cost)
    # ------------------------------------------------------------------

    def _compute_importance(
        self,
        content: str,
        emotion: EmotionState | None = None,
    ) -> float:
        """Compute importance score (0-1) for a memory entry.

        Factors:
        - Base: 0.5
        - Value resonance: +0.1 per persona core value mentioned (max 0.3)
        - Emotional intensity: +0.2 * intensity
        """
        base = 0.5
        value_score = 0.0
        if self.persona and self.persona.core_values:
            hits = sum(1 for v in self.persona.core_values if v in content)
            value_score = min(0.3, hits * 0.1)

        emotion_score = 0.0
        if emotion:
            emotion_score = emotion.intensity * 0.2

        return min(1.0, base + value_score + emotion_score)

    def _reinforce_values_from_content(self, content: str) -> None:
        """Reinforce persona values that appear in the content."""
        if not self.persona:
            return
        for value in self.persona.core_values:
            if value in content:
                self._profile.reinforce_value(value, delta=0.02)

    @staticmethod
    def _extract_keywords(content: str) -> list[str]:
        """Simple keyword extraction for diary entries."""
        # Very basic: use significant words (length >= 2) that aren't common fillers
        fillers = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "这", "那", "有", "和", "就", "都", "而", "及", "与", "或", "但是", "因为", "所以", "如果", "那么", "虽然", "但是"}
        words = []
        for w in content.replace("，", " ").replace("。", " ").replace("！", " ").replace("？", " ").split():
            if len(w) >= 2 and w not in fillers:
                words.append(w)
        return words[:8]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Persist autobiographical memory to disk."""
        self._file_store.save(self._self_memory)

    def to_dict(self) -> dict[str, Any]:
        return {
            "self_memory": self._self_memory.to_dict(),
            "self_profile": self._profile.to_dict(),
        }
