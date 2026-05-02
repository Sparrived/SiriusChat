"""Diary entry consolidation: find similar entries and merge them via LLM."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.diary.indexer import DiaryIndexer
from sirius_chat.memory.diary.manager import DiaryManager
from sirius_chat.memory.diary.models import DiaryEntry

logger = logging.getLogger(__name__)

_MERGE_SYSTEM_PROMPT = """你是群聊日记整理助手。请将以下几条相似日记整合为一条完整的日记。

要求：
1. 保留所有关键事件、人物、时间点和具体细节，不要省略
2. 按时间线组织，清晰展现事情的发展脉络
3. 去除完全重复的内容，但不要过度精简
4. 不要编造不存在的信息

输出严格 JSON 格式（不要加 markdown 代码块）：
{
  "content": "整合后的日记正文",
  "summary": "一句话摘要",
  "keywords": ["关键词1", "关键词2"]
}"""


class DiaryConsolidator:
    """Find semantically similar diary entries and merge them via LLM."""

    def __init__(self, manager: DiaryManager, config: dict[str, Any] | None = None) -> None:
        self.manager = manager
        self.config = dict(config or {})
        self.threshold = float(self.config.get("diary_merge_similarity_threshold", 0.82))
        self.min_entries = int(self.config.get("diary_consolidation_min_entries", 3))
        self.max_cluster_size = int(self.config.get("diary_consolidation_max_cluster_size", 8))

    def find_clusters(self, group_id: str) -> list[list[DiaryEntry]]:
        """Find groups of similar diary entries for *group_id*.

        Returns a list of clusters, where each cluster contains 2+ entries
        with pairwise cosine similarity >= threshold.
        """
        entries = self.manager.get_entries_for_group(group_id)
        if len(entries) < self.min_entries:
            return []

        # Only consider entries that have embeddings
        indexed = [(i, e) for i, e in enumerate(entries) if e.embedding]
        if len(indexed) < 2:
            return []

        n = len(indexed)

        # Pre-compute similarity matrix for strict pair-wise clustering
        sim_matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = DiaryIndexer._cosine_sim(
                    indexed[i][1].embedding or [],
                    indexed[j][1].embedding or [],
                )
                sim_matrix[i][j] = sim
                sim_matrix[j][i] = sim

        clusters: list[list[DiaryEntry]] = []
        used: set[int] = set()

        for a in range(n):
            if a in used:
                continue
            cluster_indices = [a]
            for b in range(a + 1, n):
                if b in used:
                    continue
                # Strict: b must be similar to ALL existing members in the cluster
                if all(sim_matrix[b][c] >= self.threshold for c in cluster_indices):
                    cluster_indices.append(b)
            if len(cluster_indices) >= 2:
                # Cap cluster size to avoid overly large prompts
                if len(cluster_indices) > self.max_cluster_size:
                    # Keep the most similar ones to the first entry
                    scored = [
                        (b, sim_matrix[a][b])
                        for b in cluster_indices[1:]
                    ]
                    scored.sort(key=lambda x: x[1], reverse=True)
                    cluster_indices = [a] + [b for b, _ in scored[: self.max_cluster_size - 1]]
                for idx in cluster_indices:
                    used.add(idx)
                clusters.append([indexed[idx][1] for idx in cluster_indices])

        return clusters

    def build_merge_prompt(self, cluster: list[DiaryEntry]) -> tuple[str, str]:
        """Build (system_prompt, user_content) for merging *cluster*."""
        lines: list[str] = []
        for i, entry in enumerate(cluster, 1):
            lines.append(f"日记{i}（{entry.created_at[:10]}）：")
            lines.append(entry.content)
            if entry.keywords:
                lines.append(f"关键词：{', '.join(entry.keywords)}")
            lines.append("")
        return _MERGE_SYSTEM_PROMPT, "\n".join(lines)

    def parse_merge_result(self, raw: str, cluster: list[DiaryEntry]) -> DiaryEntry | None:
        """Parse LLM output into a merged DiaryEntry."""
        data = self._extract_json(raw)
        if not data:
            return None

        content = str(data.get("content", "")).strip()
        summary = str(data.get("summary", "")).strip()
        keywords = data.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(k).strip() for k in keywords if str(k).strip()]

        if not content:
            return None

        # Aggregate source_ids from all merged entries
        all_source_ids: list[str] = []
        for e in cluster:
            all_source_ids.extend(e.source_ids)

        return DiaryEntry(
            entry_id=f"merged_{uuid.uuid4().hex[:12]}",
            group_id=cluster[0].group_id,
            created_at=min(
                (e.created_at for e in cluster),
                default=datetime.now(timezone.utc).isoformat(),
            ),
            source_ids=all_source_ids,
            content=content,
            keywords=keywords,
            summary=summary,
            embedding=None,  # Will be computed on add() if model available
        )

    def rebuild_entries(
        self,
        group_id: str,
        clusters: list[list[DiaryEntry]],
        merged: list[DiaryEntry],
    ) -> None:
        """Atomically replace clustered old entries with merged entries."""
        entries = self.manager.get_entries_for_group(group_id)
        clustered_ids = {e.entry_id for cluster in clusters for e in cluster}
        kept = [e for e in entries if e.entry_id not in clustered_ids]
        new_entries = kept + merged
        self.manager.replace_entries(group_id, new_entries)
        logger.info(
            "Diary consolidation for %s: merged %d clusters into %d entries, kept %d",
            group_id,
            len(clusters),
            len(merged),
            len(kept),
        )

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any] | None:
        """Extract JSON from raw LLM output, tolerating markdown fences."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try regex fallback for critical fields
        result: dict[str, Any] = {}
        m = re.search(r'"content"\s*:\s*"([^"]+)"', raw)
        if m:
            result["content"] = m.group(1)
        m = re.search(r'"summary"\s*:\s*"([^"]+)"', raw)
        if m:
            result["summary"] = m.group(1)
        m = re.search(r'"keywords"\s*:\s*(\[[^\]]*\])', raw)
        if m:
            try:
                result["keywords"] = json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return result if result.get("content") else None
