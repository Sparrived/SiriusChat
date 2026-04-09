"""Self-memory manager — diary and glossary subsystem logic."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.self.models import DiaryEntry, GlossaryTerm, SelfMemoryState

logger = logging.getLogger(__name__)

# --- Limits ---
MAX_DIARY_ENTRIES = 100
MAX_GLOSSARY_TERMS = 200
MAX_CONTEXT_EXAMPLES = 5
# Diary prompt budget: compact representation for system prompt
DIARY_PROMPT_MAX_ENTRIES = 8
GLOSSARY_PROMPT_MAX_TERMS = 20

# --- Diary decay schedule ---
# Maps age (days) → confidence retention ratio.
# Importance acts as a multiplier: high-importance entries decay slower.
DIARY_DECAY_SCHEDULE: dict[int, float] = {
    3: 0.95,
    7: 0.85,
    14: 0.70,
    30: 0.50,
    60: 0.30,
    90: 0.15,
    180: 0.05,
}


class SelfMemoryManager:
    """Manages the AI's self-memory (diary + glossary).

    Thread-safe for single-async-task usage (no internal locking).
    """

    def __init__(self, state: SelfMemoryState | None = None) -> None:
        self._state = state or SelfMemoryState()

    # ── Properties ──

    @property
    def state(self) -> SelfMemoryState:
        return self._state

    @property
    def diary_entries(self) -> list[DiaryEntry]:
        return self._state.diary_entries

    @property
    def glossary_terms(self) -> dict[str, GlossaryTerm]:
        return self._state.glossary_terms

    # ── Diary subsystem ──

    def add_diary_entry(self, entry: DiaryEntry) -> None:
        """Add a new diary entry, enforcing the capacity limit."""
        self._state.diary_entries.append(entry)
        if len(self._state.diary_entries) > MAX_DIARY_ENTRIES:
            self._evict_weakest_diary_entries()

    def apply_diary_decay(self, decay_schedule: dict[int, float] | None = None) -> int:
        """Apply time-based confidence decay to all diary entries.

        Returns the number of entries removed (confidence → 0).
        """
        schedule = decay_schedule or DIARY_DECAY_SCHEDULE
        sorted_days = sorted(schedule.keys())
        removed = 0

        surviving: list[DiaryEntry] = []
        for entry in self._state.diary_entries:
            age = entry.age_days()
            # Base decay ratio from schedule
            decay_ratio = 1.0
            for day_threshold in sorted_days:
                if age >= day_threshold:
                    decay_ratio = schedule[day_threshold]
                else:
                    break

            # Importance slows decay: importance=1.0 → 40% slower decay
            importance_factor = 1.0 - 0.4 * entry.importance
            effective_decay = 1.0 - (1.0 - decay_ratio) * importance_factor

            # Mention reinforcement: each mention adds ~5% retention
            mention_boost = min(0.25, entry.mention_count * 0.05)
            effective_decay = min(1.0, effective_decay + mention_boost)

            new_confidence = entry.confidence * effective_decay
            new_confidence = max(0.0, min(1.0, new_confidence))

            if new_confidence < 0.05:
                removed += 1
                continue

            entry.confidence = new_confidence
            surviving.append(entry)

        self._state.diary_entries = surviving
        return removed

    def reinforce_diary_entry(self, entry_id: str) -> bool:
        """Reinforce a diary entry (bump mention count + confidence)."""
        for entry in self._state.diary_entries:
            if entry.entry_id == entry_id:
                entry.mention_count += 1
                entry.confidence = min(1.0, entry.confidence + 0.1)
                return True
        return False

    def get_relevant_diary_entries(
        self,
        keywords: list[str] | None = None,
        max_entries: int = DIARY_PROMPT_MAX_ENTRIES,
    ) -> list[DiaryEntry]:
        """Retrieve diary entries most relevant to current context.

        Scoring: confidence * importance * keyword_overlap_bonus.
        """
        kw_set = {k.lower() for k in (keywords or [])}

        def _score(entry: DiaryEntry) -> float:
            base = entry.confidence * (0.5 + 0.5 * entry.importance)
            if kw_set:
                overlap = sum(1 for k in entry.keywords if k.lower() in kw_set)
                base += 0.15 * min(overlap, 3)
            return base

        scored = sorted(self._state.diary_entries, key=_score, reverse=True)
        return scored[:max_entries]

    def _evict_weakest_diary_entries(self) -> None:
        """Remove lowest-scoring entries to stay within MAX_DIARY_ENTRIES."""
        if len(self._state.diary_entries) <= MAX_DIARY_ENTRIES:
            return
        scored = sorted(
            self._state.diary_entries,
            key=lambda e: e.confidence * e.importance,
            reverse=True,
        )
        self._state.diary_entries = scored[:MAX_DIARY_ENTRIES]

    # ── Glossary subsystem ──

    def add_or_update_term(self, term: GlossaryTerm) -> None:
        """Add or merge a glossary term."""
        key = term.term.lower().strip()
        if not key:
            return

        existing = self._state.glossary_terms.get(key)
        if existing is not None:
            # Merge: keep higher confidence definition, combine examples
            existing.usage_count += 1
            existing.last_updated_at = datetime.now(timezone.utc).isoformat()
            if term.confidence > existing.confidence:
                existing.definition = term.definition
                existing.confidence = term.confidence
                existing.source = term.source
            # Merge examples (dedup, cap)
            seen = set(existing.context_examples)
            for ex in term.context_examples:
                if ex not in seen and len(existing.context_examples) < MAX_CONTEXT_EXAMPLES:
                    existing.context_examples.append(ex)
                    seen.add(ex)
            # Merge related terms
            related_set = set(existing.related_terms)
            for rt in term.related_terms:
                if rt not in related_set:
                    existing.related_terms.append(rt)
                    related_set.add(rt)
            if term.domain != "custom":
                existing.domain = term.domain
        else:
            self._state.glossary_terms[key] = term

        # Enforce capacity
        if len(self._state.glossary_terms) > MAX_GLOSSARY_TERMS:
            self._evict_least_used_terms()

    def get_term(self, term: str) -> GlossaryTerm | None:
        return self._state.glossary_terms.get(term.lower().strip())

    def get_relevant_terms(
        self,
        text: str,
        max_terms: int = GLOSSARY_PROMPT_MAX_TERMS,
    ) -> list[GlossaryTerm]:
        """Find glossary terms mentioned in or relevant to the given text."""
        text_lower = text.lower()
        matched: list[tuple[float, GlossaryTerm]] = []
        for term in self._state.glossary_terms.values():
            if term.term.lower() in text_lower:
                score = term.confidence * (1.0 + 0.1 * min(term.usage_count, 10))
                matched.append((score, term))
        matched.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in matched[:max_terms]]

    def _evict_least_used_terms(self) -> None:
        """Remove lowest-value terms to stay within MAX_GLOSSARY_TERMS."""
        if len(self._state.glossary_terms) <= MAX_GLOSSARY_TERMS:
            return
        scored = sorted(
            self._state.glossary_terms.items(),
            key=lambda kv: kv[1].confidence * kv[1].usage_count,
            reverse=True,
        )
        self._state.glossary_terms = dict(scored[:MAX_GLOSSARY_TERMS])

    # ── Prompt generation helpers ──

    def build_diary_prompt_section(
        self,
        keywords: list[str] | None = None,
        max_entries: int = DIARY_PROMPT_MAX_ENTRIES,
    ) -> str:
        """Build a compact diary section for the system prompt."""
        entries = self.get_relevant_diary_entries(keywords=keywords, max_entries=max_entries)
        if not entries:
            return ""
        lines: list[str] = []
        for entry in entries:
            # Compact: [category] content (importance_indicator)
            imp_tag = "!" if entry.importance >= 0.7 else ""
            conf_tag = "?" if entry.confidence < 0.5 else ""
            kw_str = ",".join(entry.keywords[:3]) if entry.keywords else ""
            prefix = f"[{entry.category}]" if entry.category != "observation" else ""
            line = f"{prefix}{imp_tag} {entry.content[:120]}{conf_tag}"
            if kw_str:
                line += f" #{kw_str}"
            lines.append(line.strip())
        return "\n".join(lines)

    def build_glossary_prompt_section(
        self,
        text: str = "",
        max_terms: int = GLOSSARY_PROMPT_MAX_TERMS,
    ) -> str:
        """Build a compact glossary section for the system prompt."""
        if text:
            terms = self.get_relevant_terms(text, max_terms=max_terms)
        else:
            # Return highest-confidence terms
            all_terms = sorted(
                self._state.glossary_terms.values(),
                key=lambda t: t.confidence * t.usage_count,
                reverse=True,
            )
            terms = all_terms[:max_terms]

        if not terms:
            return ""
        lines: list[str] = []
        for term in terms:
            conf_tag = "?" if term.confidence < 0.6 else ("~" if term.confidence < 0.8 else "")
            defn = term.definition[:100] if term.definition else "待明确"
            lines.append(f"{term.term}{conf_tag}: {defn}")
        return "\n".join(lines)

    # ── Serialization ──

    def to_dict(self) -> dict:
        return self._state.to_dict()

    @classmethod
    def from_dict(cls, data: dict) -> SelfMemoryManager:
        state = SelfMemoryState.from_dict(data)
        return cls(state=state)
