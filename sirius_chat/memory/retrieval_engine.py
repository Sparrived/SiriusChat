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

import math
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.activation_engine import ActivationEngine
from sirius_chat.memory.episodic.manager import EpisodicMemoryManager
from sirius_chat.memory.semantic.manager import SemanticMemoryManager
from sirius_chat.memory.working.manager import WorkingMemoryManager


class MemoryRetriever:
    """Unified memory retrieval interface."""

    def __init__(
        self,
        working_mgr: WorkingMemoryManager | None = None,
        episodic_mgr: EpisodicMemoryManager | None = None,
        semantic_mgr: SemanticMemoryManager | None = None,
        activation_engine: ActivationEngine | None = None,
    ) -> None:
        self.working = working_mgr
        self.episodic = episodic_mgr
        self.semantic = semantic_mgr
        self.activation = activation_engine or ActivationEngine()

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
        """Placeholder for semantic similarity search.

        Requires sentence-transformers. MVP falls back to keyword search.
        """
        # TODO: Implement vector embedding + cosine similarity when
        # sentence-transformers is available.
        return []

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
