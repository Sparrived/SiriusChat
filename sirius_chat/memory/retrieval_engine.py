"""Memory retrieval engine: three-tier retrieval system.

1. Working memory (in-memory, keyword match)
2. Keyword search (episodic memory files, synonym expansion)
3. Semantic similarity (optional, via sentence-transformers)
4. User profile lookup (semantic memory layer)

Scoring:
    score = importance * 0.4 + recency_score * 0.3 + activation * 0.3
    recency_score = exp(-0.1 * days_since_creation)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.activation_engine import ActivationEngine
from sirius_chat.memory.episodic.manager import EpisodicMemoryManager
from sirius_chat.memory.semantic.manager import SemanticMemoryManager
from sirius_chat.memory.working.manager import WorkingMemoryManager

logger = logging.getLogger(__name__)

# Optional sentence-transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer, util

    _ST_AVAILABLE = True
except Exception:  # pragma: no cover
    _ST_AVAILABLE = False


class MemoryRetriever:
    """Unified memory retrieval interface.

    Semantic search requires ``sentence-transformers`` (optional dependency).
    If not installed, semantic tier gracefully falls back to keyword search.
    """

    def __init__(
        self,
        working_mgr: WorkingMemoryManager | None = None,
        episodic_mgr: EpisodicMemoryManager | None = None,
        semantic_mgr: SemanticMemoryManager | None = None,
        activation_engine: ActivationEngine | None = None,
        semantic_model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.working = working_mgr
        self.episodic = episodic_mgr
        self.semantic = semantic_mgr
        self.activation = activation_engine or ActivationEngine()

        # Lazy-load sentence-transformers model
        self._semantic_model_name = semantic_model_name
        self._semantic_model: Any | None = None
        if _ST_AVAILABLE:
            try:
                self._semantic_model = SentenceTransformer(semantic_model_name)
                logger.info("Semantic search enabled with model: %s", semantic_model_name)
            except Exception as exc:
                logger.warning("Failed to load sentence-transformers model: %s", exc)

    @property
    def semantic_available(self) -> bool:
        """Whether semantic similarity search is available."""
        return self._semantic_model is not None

    async def retrieve(
        self,
        query: str,
        group_id: str,
        *,
        user_id: str | None = None,
        top_k: int = 5,
        enable_semantic: bool = False,
    ) -> list[dict[str, Any]]:
        """Multi-strategy memory retrieval.

        Returns scored results sorted by relevance.
        """
        results: list[dict[str, Any]] = []

        # 1. Working memory (highest priority)
        if self.working:
            wm_results = self._search_working_memory(group_id, query, user_id)
            results.extend(wm_results)

        # 2. Keyword search episodic memory
        if self.episodic:
            kw_results = self._search_episodic_keywords(group_id, query, user_id)
            results.extend(kw_results)

        # 3. Semantic similarity (optional)
        if enable_semantic and self.episodic:
            sem_results = await self._search_semantic(group_id, query, user_id, top_k)
            results.extend(sem_results)

        # 4. User semantic profile
        if self.semantic and user_id:
            profile_results = self._search_user_profile(group_id, user_id, query)
            results.extend(profile_results)

        # Deduplicate and score
        return self._deduplicate_and_score(results, query, top_k)

    def _search_working_memory(
        self,
        group_id: str,
        query: str,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        if not self.working:
            return []
        entries = self.working.get_window(group_id)
        query_lower = query.lower()
        results = []
        for e in entries:
            if user_id and e.user_id != user_id:
                continue
            if query_lower in e.content.lower():
                results.append({
                    "source": "working_memory",
                    "content": e.content,
                    "user_id": e.user_id,
                    "timestamp": e.timestamp,
                    "importance": e.importance,
                    "activation": 1.0,  # Working memory is always "active"
                    "entry_id": e.entry_id,
                })
        return results

    def _search_episodic_keywords(
        self,
        group_id: str,
        query: str,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        if not self.episodic:
            return []
        entries = self.episodic.search_by_keyword(group_id, query, limit=20)
        results = []
        for e in entries:
            if user_id and e.user_id != user_id:
                continue
            activation = self.activation.calculate_activation(
                importance=e.confidence,
                created_at=e.created_at,
                access_count=getattr(e, "access_count", 0),
                memory_category=e.category,
            )
            results.append({
                "source": "episodic_memory",
                "content": e.summary,
                "user_id": e.user_id,
                "timestamp": e.created_at,
                "importance": e.confidence,
                "activation": activation,
                "event_id": e.event_id,
            })
        return results

    async def _search_semantic(
        self,
        group_id: str,
        query: str,
        user_id: str | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Semantic similarity search using sentence-transformers embeddings.

        Falls back to empty list if sentence-transformers is unavailable.
        """
        if self._semantic_model is None or self.episodic is None:
            return []

        # Load all events for the group
        events = self.episodic.list_events(group_id)
        if not events:
            return []

        # Filter by user if specified
        if user_id:
            events = [e for e in events if getattr(e, "user_id", "") == user_id]

        if not events:
            return []

        # Build candidate texts
        texts = []
        for e in events:
            content = getattr(e, "content", "")
            summary = getattr(e, "summary", "")
            texts.append(f"{summary} {content}".strip() or "event")

        # Encode query and candidates
        try:
            query_embedding = self._semantic_model.encode(query, convert_to_tensor=True)
            event_embeddings = self._semantic_model.encode(texts, convert_to_tensor=True)
            similarities = util.cos_sim(query_embedding, event_embeddings)[0]
        except Exception as exc:
            logger.warning("Semantic encoding failed: %s", exc)
            return []

        # Collect results above threshold
        threshold = 0.35
        results = []
        for idx, score in enumerate(similarities.tolist()):
            if score < threshold:
                continue
            event = events[idx]
            results.append({
                "source": "semantic",
                "content": getattr(event, "content", ""),
                "score": float(score),
                "timestamp": getattr(event, "timestamp", ""),
                "user_id": getattr(event, "user_id", ""),
            })

        # Sort by similarity score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _search_user_profile(
        self,
        group_id: str,
        user_id: str,
        query: str,
    ) -> list[dict[str, Any]]:
        if not self.semantic:
            return []
        profile = self.semantic.get_user_profile(group_id, user_id)
        if not profile:
            return []
        query_lower = query.lower()
        results = []
        # Search base attributes
        for key, value in profile.base_attributes.items():
            text = f"{key}: {value}"
            if query_lower in text.lower():
                results.append({
                    "source": "semantic_profile",
                    "content": text,
                    "user_id": user_id,
                    "timestamp": profile.updated_at,
                    "importance": 0.8,
                    "activation": 1.0,
                })
        # Search interest graph
        for node in profile.interest_graph:
            if query_lower in node.topic.lower():
                results.append({
                    "source": "semantic_profile",
                    "content": f"兴趣: {node.topic} (参与度{node.participation:.2f})",
                    "user_id": user_id,
                    "timestamp": profile.updated_at,
                    "importance": 0.7,
                    "activation": 1.0,
                })
        return results

    def _deduplicate_and_score(
        self,
        results: list[dict[str, Any]],
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Deduplicate by content and compute composite score."""
        seen: set[str] = set()
        scored = []
        for r in results:
            content = str(r.get("content", ""))
            if content in seen:
                continue
            seen.add(content)

            importance = float(r.get("importance", 0.5))
            activation = float(r.get("activation", 1.0))
            recency_score = self._recency_score(str(r.get("timestamp", "")))
            score = importance * 0.4 + recency_score * 0.3 + activation * 0.3
            scored.append({**r, "score": round(score, 4)})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _recency_score(timestamp: str) -> float:
        if not timestamp:
            return 0.5
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days = (now - dt).total_seconds() / 86400.0
            return math.exp(-0.1 * max(0.0, days))
        except (ValueError, TypeError):
            return 0.5
