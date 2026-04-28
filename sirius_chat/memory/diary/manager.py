"""Diary manager: orchestrates generation, indexing, storage, and retrieval."""

from __future__ import annotations

import logging
from typing import Any

from sirius_chat.memory.basic.models import BasicMemoryEntry
from sirius_chat.memory.diary.generator import DiaryGenerator
from sirius_chat.memory.diary.indexer import DiaryIndexer, DiaryRetriever
from sirius_chat.memory.diary.models import DiaryEntry, DiaryGenerationResult
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
        # Track which groups have been loaded from disk (lazy loading)
        self._loaded_groups: set[str] = set()

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
    ) -> DiaryGenerationResult | None:
        """Generate a diary entry from candidates and index it."""
        if not candidates:
            return None

        result = await self._generator.generate(
            group_id=group_id,
            candidates=candidates,
            persona_name=persona_name,
            persona_description=persona_description,
            provider_async=provider_async,
            model_name=model_name,
        )
        if result is None:
            return None

        self.add_entry(group_id, result.entry)

        # Mark sources as diarized
        sources = self._diarized_sources.setdefault(group_id, set())
        sources.update(result.entry.source_ids)

        logger.info(
            "群 %s 的日记写好了，总结了 %d 条对话。",
            group_id,
            len(result.entry.source_ids),
        )
        return result

    def ensure_group_loaded(self, group_id: str) -> None:
        """Lazy-load persisted entries for a group if not already loaded.

        Safe to call multiple times (idempotent).  This is the entry point
        for external callers (e.g. EmotionalGroupChatEngine) to warm up
        the diary index before retrieval.
        """
        if group_id in self._loaded_groups:
            return
        self.load_group(group_id)
        self._loaded_groups.add(group_id)

    def is_source_diarized(self, group_id: str, entry_id: str) -> bool:
        """Check if a basic memory entry has already been processed into a diary."""
        self.ensure_group_loaded(group_id)
        return entry_id in self._diarized_sources.get(group_id, set())

    # ------------------------------------------------------------------
    # Index / Store
    # ------------------------------------------------------------------

    def add_entry(self, group_id: str, entry: DiaryEntry) -> None:
        """Add an entry to memory index and persist."""
        self.ensure_group_loaded(group_id)
        self._indexer.add(entry)
        existing = self._store.load(group_id)
        existing.append(entry)
        self._store.save(group_id, existing)

    def load_group(self, group_id: str) -> None:
        """Load persisted entries for a group into the index."""
        entries = self._store.load(group_id)
        logger.info("群 %s 日记加载中: 磁盘条目=%d", group_id, len(entries))
        any_recomputed = False
        for entry in entries:
            if self._indexer.add(entry):
                any_recomputed = True
            sources = self._diarized_sources.setdefault(group_id, set())
            sources.update(entry.source_ids)
        # If any stale embeddings were recomputed (e.g. model swap),
        # persist the updated entries so the migration happens only once.
        if any_recomputed:
            self._store.save(group_id, entries)
            logger.info("群 %s 的日记 embedding 已自动迁移并持久化", group_id)
        logger.info("群 %s 日记加载完成: 索引条目=%d", group_id, len(entries))

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        group_id: str | None = None,
        top_k: int = 5,
        max_tokens_budget: int = 800,
    ) -> list[DiaryEntry]:
        """Retrieve relevant diary entries.

        Args:
            query: Search query.
            group_id: If provided, lazy-load this group's entries before retrieval.
            top_k: Maximum number of entries to return.
            max_tokens_budget: Maximum tokens for the returned content.
        """
        if group_id is not None:
            self.ensure_group_loaded(group_id)
        results = self._retriever.retrieve(
            query=query,
            top_k=top_k,
            max_tokens_budget=max_tokens_budget,
        )
        logger.info(
            "日记检索结果: group=%s | 返回 %d 条 (预算 %d tokens)",
            group_id,
            len(results),
            max_tokens_budget,
        )
        return results

    # ------------------------------------------------------------------
    # Consolidation helpers
    # ------------------------------------------------------------------

    def get_entries_for_group(self, group_id: str) -> list[DiaryEntry]:
        """Get all indexed entries for a group."""
        self.ensure_group_loaded(group_id)
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
