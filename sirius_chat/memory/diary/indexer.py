"""Diary indexer: semantic embedding and RAG retrieval."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any

# 阻断 transformers 后台自动连接 HuggingFace Hub（避免国内网络超时）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from sirius_chat.memory.diary.models import DiaryEntry
from sirius_chat.memory.diary.vector_store import DiaryVectorStore

logger = logging.getLogger(__name__)

# Optional sentence-transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer, util

    _ST_AVAILABLE = True
except Exception:  # pragma: no cover
    _ST_AVAILABLE = False

# Module-level model singleton: avoids reloading the model every time
# DiaryIndexer is recreated (e.g. on plugin reload / engine rebuild).
_MODEL_SINGLETON: dict[str, Any] = {}


class DiaryIndexer:
    """Semantic index for diary entries with persistent vector store.

    Uses ChromaDB for persistent vector storage and sentence-transformers
    for embedding computation. Falls back to keyword-only search when
    semantic components are unavailable.
    """

    MODEL_NAME: str = "BAAI/bge-small-zh"

    def __init__(
        self,
        enable_semantic: bool = True,
        vector_store: DiaryVectorStore | None = None,
    ) -> None:
        self._entries: list[DiaryEntry] = []
        self._model: Any | None = None
        self._embedding_dim: int | None = None
        self._enable_semantic = enable_semantic
        self._vector_store = vector_store

        if not enable_semantic:
            logger.debug("日记语义索引已禁用（enable_semantic=False）")
        elif not _ST_AVAILABLE:
            logger.warning(
                "sentence-transformers 未安装，日记检索将退化为纯关键词匹配。"
                "如需语义搜索请安装: pip install sentence-transformers"
            )

    def _ensure_model_loaded(self) -> None:
        """Lazy-load the embedding model on first use."""
        if self._model is not None or not self._enable_semantic or not _ST_AVAILABLE:
            return
        cached = _MODEL_SINGLETON.get(self.MODEL_NAME)
        if cached is not None:
            self._model = cached
            self._embedding_dim = getattr(
                self._model, "get_embedding_dimension", lambda: None
            )()
            logger.info(
                "日记语义索引复用已缓存模型 %s (dim=%s)",
                self.MODEL_NAME,
                self._embedding_dim,
            )
        else:
            self._model = self._load_model_local_first(self.MODEL_NAME)
            if self._model is not None:
                _MODEL_SINGLETON[self.MODEL_NAME] = self._model
                self._embedding_dim = getattr(
                    self._model, "get_embedding_dimension", lambda: None
                )()
                logger.info(
                    "日记语义索引已加载模型 %s (dim=%s)",
                    self.MODEL_NAME,
                    self._embedding_dim,
                )

    @staticmethod
    def _load_model_local_first(model_name: str) -> Any | None:
        """Load SentenceTransformer from local cache only (no network).

        Uses ``local_files_only=True`` to forcefully block any outgoing
        HuggingFace Hub requests. If the model is not present locally,
        loading fails immediately rather than falling back to download.
        """
        try:
            model = SentenceTransformer(model_name, local_files_only=True)
            logger.info("模型 %s 从本地缓存加载", model_name)
            return model
        except Exception as exc:
            logger.warning("日记索引模型本地加载失败: %s", exc)
            return None

    @property
    def semantic_available(self) -> bool:
        return self._model is not None

    def add(self, entry: DiaryEntry) -> bool:
        """Add an entry to the index, computing embedding if possible.

        If an existing embedding's dimension does not match the current model
        (e.g. after switching from all-MiniLM-L6-v2 to bge-small-zh), it is
        discarded and recomputed automatically.

        Returns True if an embedding was computed or recomputed.
        """
        self._ensure_model_loaded()
        recomputed = False
        if self._model is not None:
            # Detect stale embeddings from a previous model and force recompute
            if entry.embedding and self._embedding_dim is not None:
                if len(entry.embedding) != self._embedding_dim:
                    logger.info(
                        "日记 embedding 维度变更 (%d -> %d)，将重新计算: %s",
                        len(entry.embedding),
                        self._embedding_dim,
                        entry.entry_id,
                    )
                    entry.embedding = None

            if not entry.embedding:
                try:
                    vec = self._model.encode(entry.content, convert_to_tensor=False)
                    entry.embedding = [float(v) for v in vec]
                    recomputed = True
                    logger.info("日记 embedding 已重新计算: %s", entry.entry_id)
                except Exception as exc:
                    logger.warning("日记 embedding 计算失败: %s | %s", entry.entry_id, exc)

        # Persist to vector store if available
        if self._vector_store is not None and self._vector_store.available:
            self._vector_store.add(entry)

        self._entries.append(entry)
        return recomputed

    def search(
        self,
        query: str,
        top_k: int = 5,
        group_id: str = "",
    ) -> list[tuple[DiaryEntry, float]]:
        """Hybrid search: fuse semantic similarity + keyword matching.

        If *group_id* is provided, only entries belonging to that group
        are considered. Returns list of (entry, score) sorted by score
        descending.
        """
        self._ensure_model_loaded()
        entries = self._entries
        if group_id:
            entries = [e for e in entries if e.group_id == group_id]
        if not entries:
            logger.debug("日记检索: group=%s 无条目可检索", group_id)
            return []

        # Compute both semantic and keyword scores on the full candidate set
        semantic_scores: dict[str, float] = {}
        if self._model is not None:
            # Prefer vector store for semantic search if available
            if self._vector_store is not None and self._vector_store.available and group_id:
                try:
                    query_vec = self._model.encode(query, convert_to_tensor=False)
                    for eid, score in self._vector_store.search(query_vec, group_id, top_k=top_k * 2):
                        semantic_scores[eid] = score
                except Exception as exc:
                    logger.warning("向量存储检索失败，回退到内存检索: %s", exc)
                    for entry, score in self._semantic_search(query, len(entries), entries):
                        semantic_scores[entry.entry_id] = score
            else:
                for entry, score in self._semantic_search(query, len(entries), entries):
                    semantic_scores[entry.entry_id] = score

        keyword_scores: dict[str, float] = {}
        for entry, score in self._keyword_search(query, len(entries), entries):
            keyword_scores[entry.entry_id] = score

        # Fuse: semantic 60% + keyword 40% (keyword normalized to [0, 1])
        fused: list[tuple[DiaryEntry, float]] = []
        for entry in entries:
            s = semantic_scores.get(entry.entry_id, 0.0)
            k = keyword_scores.get(entry.entry_id, 0.0)
            # Keyword raw score can exceed 1.0 (1.0 content + 0.8 summary + 0.5*keywords)
            # Soft-cap at 2.0 and compress to [0, 1]
            final = 0.6 * s + 0.4 * min(k / 2.0, 1.0)
            if final > 0.05:
                fused.append((entry, final))

        fused.sort(key=lambda x: x[1], reverse=True)
        result = fused[:top_k]
        logger.info(
            "日记检索: group=%s query=%.20s... | 候选=%d | 语义=%s | 返回=%d 条",
            group_id,
            query,
            len(entries),
            "开" if self._model is not None else "关",
            len(result),
        )
        return result

    def _semantic_search(
        self,
        query: str,
        top_k: int,
        entries: list[DiaryEntry],
    ) -> list[tuple[DiaryEntry, float]]:
        try:
            query_vec = self._model.encode(query, convert_to_tensor=False)
        except Exception as exc:
            logger.warning("Query embedding 失败: %s", exc)
            return self._keyword_search(query, top_k, entries)

        scored: list[tuple[DiaryEntry, float]] = []
        for entry in entries:
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

    def _keyword_search(
        self,
        query: str,
        top_k: int,
        entries: list[DiaryEntry],
    ) -> list[tuple[DiaryEntry, float]]:
        query_lower = query.lower()
        scored: list[tuple[DiaryEntry, float]] = []
        for entry in entries:
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
        removed_ids: list[str] = []
        new_entries: list[DiaryEntry] = []
        for e in self._entries:
            if set(e.source_ids) & source_ids:
                removed_ids.append(e.entry_id)
            else:
                new_entries.append(e)
        self._entries = new_entries

        # Also remove from vector store
        if removed_ids and self._vector_store is not None and self._vector_store.available:
            # Group removed_ids by group_id
            by_group: dict[str, list[str]] = {}
            for e in self._entries:
                pass  # We already removed them from _entries
            # Need to get group_id from removed entries; iterate original list
            for e in self._entries + new_entries:
                pass
            # Simpler: just clear and re-add remaining entries for affected groups
            affected_groups = {e.group_id for e in self._entries + new_entries}
            for gid in affected_groups:
                self._vector_store.clear_group(gid)
            for e in self._entries:
                self._vector_store.add(e)

        return original - len(self._entries)

    def list_all(self) -> list[DiaryEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        if self._vector_store is not None and self._vector_store.available:
            # Cannot clear all groups easily; just reinitialize
            pass


class DiaryRetriever:
    """High-level retriever with token budget management."""

    def __init__(self, indexer: DiaryIndexer) -> None:
        self._indexer = indexer

    def retrieve(
        self,
        query: str,
        *,
        group_id: str = "",
        top_k: int = 5,
        max_tokens_budget: int = 800,
    ) -> list[DiaryEntry]:
        """Retrieve relevant diary entries within token budget.

        Approximates 1 token ≈ 1.5 Chinese characters or 0.75 English words.
        If *group_id* is provided, only entries from that group are returned.
        """
        results = self._indexer.search(query, top_k=top_k, group_id=group_id)
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
