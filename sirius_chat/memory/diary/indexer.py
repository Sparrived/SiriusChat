"""Diary indexer: semantic embedding and RAG retrieval."""

from __future__ import annotations

import logging
import math
from typing import Any

from sirius_chat.memory.diary.models import DiaryEntry

logger = logging.getLogger(__name__)

# Optional sentence-transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer, util

    _ST_AVAILABLE = True
except Exception:  # pragma: no cover
    _ST_AVAILABLE = False


class DiaryIndexer:
    """In-memory semantic index for diary entries.

    Uses sentence-transformers if available; otherwise falls back to
    keyword-only search.
    """

    MODEL_NAME: str = "all-MiniLM-L6-v2"

    def __init__(self) -> None:
        self._entries: list[DiaryEntry] = []
        self._model: Any | None = None
        if _ST_AVAILABLE:
            try:
                self._model = SentenceTransformer(self.MODEL_NAME)
                logger.info("日记语义索引已加载模型 %s", self.MODEL_NAME)
            except Exception as exc:
                logger.warning("日记索引模型加载失败: %s", exc)

    @property
    def semantic_available(self) -> bool:
        return self._model is not None

    def add(self, entry: DiaryEntry) -> None:
        """Add an entry to the index, computing embedding if possible."""
        if self._model is not None and not entry.embedding:
            try:
                vec = self._model.encode(entry.content, convert_to_tensor=False)
                entry.embedding = [float(v) for v in vec]
            except Exception as exc:
                logger.debug("Embedding 计算失败: %s", exc)
        self._entries.append(entry)

    def search(self, query: str, top_k: int = 5) -> list[tuple[DiaryEntry, float]]:
        """Search entries by semantic similarity or keyword fallback.

        Returns list of (entry, score) sorted by score descending.
        """
        if not self._entries:
            return []

        if self._model is not None:
            return self._semantic_search(query, top_k)
        return self._keyword_search(query, top_k)

    def _semantic_search(self, query: str, top_k: int) -> list[tuple[DiaryEntry, float]]:
        try:
            query_vec = self._model.encode(query, convert_to_tensor=False)
        except Exception as exc:
            logger.warning("Query embedding 失败: %s", exc)
            return self._keyword_search(query, top_k)

        scored: list[tuple[DiaryEntry, float]] = []
        for entry in self._entries:
            if not entry.embedding:
                continue
            # Cosine similarity via dot product of normalized vectors
            score = self._cosine_sim(query_vec, entry.embedding)
            if score > 0.25:  # minimum relevance threshold
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _keyword_search(self, query: str, top_k: int) -> list[tuple[DiaryEntry, float]]:
        query_lower = query.lower()
        scored: list[tuple[DiaryEntry, float]] = []
        for entry in self._entries:
            score = 0.0
            if query_lower in entry.content.lower():
                score += 1.0
            for kw in entry.keywords:
                if query_lower in kw.lower():
                    score += 0.5
            if query_lower in entry.summary.lower():
                score += 0.8
            if score > 0:
                scored.append((entry, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def remove_by_source_ids(self, source_ids: set[str]) -> int:
        """Remove entries whose source_ids overlap with the given set.

        Returns number of removed entries.
        """
        original = len(self._entries)
        self._entries = [
            e for e in self._entries
            if not set(e.source_ids) & source_ids
        ]
        return original - len(self._entries)

    def list_all(self) -> list[DiaryEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class DiaryRetriever:
    """High-level retriever with token budget management."""

    def __init__(self, indexer: DiaryIndexer) -> None:
        self._indexer = indexer

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        max_tokens_budget: int = 800,
    ) -> list[DiaryEntry]:
        """Retrieve relevant diary entries within token budget.

        Approximates 1 token ≈ 1.5 Chinese characters or 0.75 English words.
        """
        results = self._indexer.search(query, top_k=top_k)
        if not results:
            return []

        selected: list[DiaryEntry] = []
        total_chars = 0
        # Rough budget: 800 tokens ≈ 1200 chars (mixed CJK/Latin)
        char_budget = int(max_tokens_budget * 1.5)

        for entry, score in results:
            added_chars = len(entry.content) + len(entry.summary)
            if total_chars + added_chars > char_budget and selected:
                break
            selected.append(entry)
            total_chars += added_chars

        return selected
