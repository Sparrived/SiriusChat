"""Diary manager: orchestrates generation, indexing, storage, and retrieval."""

from __future__ import annotations

import logging
from typing import Any

from sirius_chat.memory.basic.models import BasicMemoryEntry
from sirius_chat.memory.diary.generator import DiaryGenerator
from sirius_chat.memory.diary.indexer import DiaryIndexer, DiaryRetriever
from sirius_chat.memory.diary.models import DiaryEntry
from sirius_chat.memory.diary.store import DiaryFileStore

logger = logging.getLogger(__name__)


class DiaryManager:
    """High-level manager for diary memory lifecycle.

    - Generates diary entries from basic memory candidates.
    - Indexes entries for semantic retrieval.
    - Persists to disk.
    """

    def __init__(self, work_path: Any) -> None:
        self._store = DiaryFileStore(work_path)
        self._indexer = DiaryIndexer()
        self._retriever = DiaryRetriever(self._indexer)
        self._generator = DiaryGenerator()
        # Track source_ids that have already been diary-ized per group
        self._diarized_sources: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate_from_candidates(
        self,
        *,
        group_id: str,
        candidates: list[BasicMemoryEntry],
        persona_name: str,
        persona_description: str,
        provider_async: Any,
        model_name: str,
    ) -> DiaryEntry | None:
        """Generate a diary entry from candidates and index it."""
        if not candidates:
            return None

        entry = await self._generator.generate(
            group_id=group_id,
            candidates=candidates,
            persona_name=persona_name,
            persona_description=persona_description,
            provider_async=provider_async,
            model_name=model_name,
        )
        if entry is None:
            return None

        self.add_entry(group_id, entry)

        # Mark sources as diarized
        sources = self._diarized_sources.setdefault(group_id, set())
        sources.update(entry.source_ids)

        logger.info(
            "群 %s 的日记写好了，总结了 %d 条对话。",
            group_id,
            len(entry.source_ids),
        )
        return entry

    def is_source_diarized(self, group_id: str, entry_id: str) -> bool:
        """Check if a basic memory entry has already been processed into a diary."""
        return entry_id in self._diarized_sources.get(group_id, set())

    # ------------------------------------------------------------------
    # Index / Store
    # ------------------------------------------------------------------

    def add_entry(self, group_id: str, entry: DiaryEntry) -> None:
        """Add an entry to memory index and persist."""
        self._indexer.add(entry)
        existing = self._store.load(group_id)
        existing.append(entry)
        self._store.save(group_id, existing)

    def load_group(self, group_id: str) -> None:
        """Load persisted entries for a group into the index."""
        entries = self._store.load(group_id)
        for entry in entries:
            self._indexer.add(entry)
            sources = self._diarized_sources.setdefault(group_id, set())
            sources.update(entry.source_ids)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_tokens_budget: int = 800,
    ) -> list[DiaryEntry]:
        """Retrieve relevant diary entries."""
        return self._retriever.retrieve(
            query=query,
            top_k=top_k,
            max_tokens_budget=max_tokens_budget,
        )

    # ------------------------------------------------------------------
    # Consolidation helpers
    # ------------------------------------------------------------------

    def get_entries_for_group(self, group_id: str) -> list[DiaryEntry]:
        """Get all indexed entries for a group."""
        return [e for e in self._indexer.list_all() if e.group_id == group_id]

    def replace_entries(self, group_id: str, new_entries: list[DiaryEntry]) -> None:
        """Replace all entries for a group (used after consolidation)."""
        # Remove old entries for this group from indexer
        old = self._store.load(group_id)
        for e in old:
            self._indexer.remove_by_source_ids(set(e.source_ids))
        # Add new entries
        for e in new_entries:
            self._indexer.add(e)
        self._store.save(group_id, new_entries)
