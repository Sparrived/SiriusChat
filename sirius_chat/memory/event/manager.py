"""Event memory manager v2 — observation-based, user-scoped, batch extraction.

Core changes from v1:
- Messages are buffered per-user, not processed individually.
- LLM batch extraction replaces per-message heuristic clustering.
- Observations are directly linked to a specific user_id.
- Simple content-similarity deduplication replaces Jaccard feature scoring.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sirius_chat.memory.event.models import EventMemoryEntry, OBSERVATION_CATEGORIES

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Tunables
# ────────────────────────────────────────────────────────────────
_MIN_CONTENT_LENGTH = 6        # 低于此长度的消息不缓冲
_MAX_BUFFER_PER_USER = 20      # 单用户最大缓冲消息数
_MAX_EVIDENCE_SAMPLES = 4      # 单条观察最大证据条数
_MAX_MESSAGE_SAMPLE_LEN = 200  # 缓冲消息截断长度
_SIMILARITY_MERGE_THRESHOLD = 0.55  # 字符集重合度高于此值视为重复

_EXTRACTION_SYSTEM_PROMPT = (
    "你是用户画像分析器。请分析参与者的对话消息，提取值得长期记住的观察信息。\n"
    "如果消息内容过于日常（问候、简短回应、无信息量），返回空 JSON 数组 []。\n"
    "请严格输出 JSON 数组，每个元素包含：\n"
    "- category: string（preference|trait|relationship|experience|emotion|goal）\n"
    "- content: string（简洁的自然语言描述，不超过50字）\n"
    "- confidence: float（0.0-1.0，信息确定度）"
)


def _build_extraction_user_prompt(user_name: str, messages: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {m}" for i, m in enumerate(messages))
    return (
        f'以下是参与者 "{user_name}" 的近期对话消息：\n'
        f"{numbered}\n\n"
        "请提取对该参与者有长期参考价值的观察（偏好、特质、关系、经历、情绪模式、目标计划）。\n"
        "如果没有有价值的信息，返回 []。"
    )


def _char_similarity(a: str, b: str) -> float:
    """Character-set Jaccard — cheap proxy for semantic overlap."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = len(sa | sb)
    return len(sa & sb) / union if union else 0.0


class EventMemoryManager:
    """Manages user-scoped observations with buffered batch extraction."""

    def __init__(self) -> None:
        self.entries: list[EventMemoryEntry] = []
        self._buffer: dict[str, list[str]] = {}   # user_id → buffered messages

    # ── id generation ──────────────────────────────────────────

    def _next_event_id(self) -> str:
        return f"evt_{len(self.entries) + 1:04d}"

    # ── message buffering ──────────────────────────────────────

    def buffer_message(self, *, user_id: str, content: str) -> None:
        """Buffer a message for later batch extraction.

        Very short / trivial messages are silently discarded.
        """
        text = content.strip()
        if len(text) < _MIN_CONTENT_LENGTH:
            return
        buf = self._buffer.setdefault(user_id, [])
        buf.append(text[:_MAX_MESSAGE_SAMPLE_LEN])
        if len(buf) > _MAX_BUFFER_PER_USER:
            buf[:] = buf[-_MAX_BUFFER_PER_USER:]

    def should_extract(self, user_id: str, batch_size: int = 5) -> bool:
        """Check whether buffered messages reached the extraction threshold."""
        return len(self._buffer.get(user_id, [])) >= batch_size

    def pending_buffer_counts(self) -> dict[str, int]:
        """Return {user_id: buffered_message_count} for diagnostics."""
        return {uid: len(msgs) for uid, msgs in self._buffer.items() if msgs}

    # ── quick relevance check (no LLM) ────────────────────────

    def check_relevance(self, *, user_id: str, content: str) -> dict[str, object]:
        """Lightweight relevance check against existing observations.

        Returns a dict compatible with the legacy *hit_payload* shape
        so ``_compute_event_relevance_score`` keeps working.
        """
        user_entries = [e for e in self.entries if e.user_id == user_id and e.verified]
        if not user_entries:
            return {"level": "new", "score": 0.0}
        best = max(_char_similarity(content, e.summary) for e in user_entries)
        if best >= 0.35:
            return {"level": "high", "score": best}
        if best >= 0.20:
            return {"level": "weak", "score": best}
        return {"level": "new", "score": best}

    # ── batch LLM extraction ──────────────────────────────────

    async def extract_observations(
        self,
        *,
        user_id: str,
        user_name: str,
        provider_async: Any,
        model_name: str,
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> list[EventMemoryEntry]:
        """Consume the buffer for *user_id* and return new/merged observations.

        ``provider_async`` must expose an async ``generate_async(request)`` method
        compatible with ``GenerationRequest``.
        """
        from sirius_chat.providers.base import GenerationRequest

        messages = self._buffer.pop(user_id, [])
        if not messages:
            return []

        request = GenerationRequest(
            model=model_name,
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _build_extraction_user_prompt(user_name, messages),
            }],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="event_extract",
        )

        try:
            raw = await provider_async.generate_async(request)
        except Exception as exc:
            logger.warning("观察提取 LLM 调用失败 (user=%s): %s", user_id, exc)
            # 放回缓冲以便下次重试
            self._buffer.setdefault(user_id, []).extend(messages)
            return []

        parsed = self._parse_extraction_response(raw)
        if not parsed:
            return []

        now_iso = datetime.now().isoformat(timespec="seconds")
        # 取最后几条消息作为证据
        evidence = messages[-_MAX_EVIDENCE_SAMPLES:]

        new_entries: list[EventMemoryEntry] = []
        for item in parsed:
            category = str(item.get("category", "custom")).strip().lower()
            if category not in OBSERVATION_CATEGORIES:
                category = "custom"
            summary_text = str(item.get("content", "")).strip()
            if not summary_text:
                continue
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))

            entry = EventMemoryEntry(
                event_id=self._next_event_id(),
                user_id=user_id,
                category=category,
                summary=summary_text[:100],
                confidence=confidence,
                evidence_samples=list(evidence),
                created_at=now_iso,
                updated_at=now_iso,
                mention_count=1,
                verified=True,  # LLM-produced — considered verified
            )
            merged = self._merge_or_add(entry)
            new_entries.append(merged)

        return new_entries

    # ── finalize (session end) ─────────────────────────────────

    async def finalize_pending_events(
        self,
        provider_async: Any,
        model_name: str,
        min_mentions: int = 3,  # kept for signature compat, unused in v2
    ) -> dict[str, Any]:
        """Flush all remaining buffers at session end.

        Signature is kept compatible with v1 for engine integration.
        Returns stats dict with verified_count / rejected_count / pending_count.
        """
        verified_count = 0
        rejected_count = 0
        for uid in list(self._buffer.keys()):
            messages = self._buffer.get(uid, [])
            if not messages:
                continue
            results = await self.extract_observations(
                user_id=uid,
                user_name=uid,   # engine should pass real name at call site
                provider_async=provider_async,
                model_name=model_name,
            )
            verified_count += len(results)
        pending_count = sum(len(v) for v in self._buffer.values())
        return {
            "verified_count": verified_count,
            "rejected_count": rejected_count,
            "pending_count": pending_count,
        }

    # ── query ──────────────────────────────────────────────────

    def top_events(
        self,
        limit: int = 5,
        include_pending: bool = False,
        user_id: str | None = None,
    ) -> list[EventMemoryEntry]:
        """Return top observations, optionally filtered by user."""
        filtered = list(self.entries)
        if user_id:
            filtered = [e for e in filtered if e.user_id == user_id]
        if not include_pending:
            filtered = [e for e in filtered if e.verified]
        else:
            filtered = [e for e in filtered if e.verified or e.mention_count >= 2]
        filtered.sort(key=lambda e: (e.updated_at, e.mention_count), reverse=True)
        return filtered[:limit]

    def get_user_observations(self, user_id: str, limit: int = 10) -> list[EventMemoryEntry]:
        """Get observations for a specific user, ordered by confidence."""
        user_entries = [e for e in self.entries if e.user_id == user_id]
        user_entries.sort(key=lambda e: (e.confidence, e.mention_count), reverse=True)
        return user_entries[:limit]

    # ── deduplication ──────────────────────────────────────────

    def _merge_or_add(self, entry: EventMemoryEntry) -> EventMemoryEntry:
        """If a similar observation for the same user+category exists, merge."""
        for existing in self.entries:
            if existing.user_id != entry.user_id:
                continue
            if existing.category != entry.category:
                continue
            if _char_similarity(existing.summary, entry.summary) < _SIMILARITY_MERGE_THRESHOLD:
                continue
            # merge
            existing.mention_count += 1
            existing.confidence = min(1.0, max(existing.confidence, entry.confidence))
            existing.updated_at = entry.updated_at
            for sample in entry.evidence_samples:
                if sample not in existing.evidence_samples:
                    existing.evidence_samples.append(sample)
            if len(existing.evidence_samples) > _MAX_EVIDENCE_SAMPLES:
                existing.evidence_samples = existing.evidence_samples[-_MAX_EVIDENCE_SAMPLES:]
            return existing
        self.entries.append(entry)
        return entry

    # ── backward-compat wrapper (v1 API) ──────────────────────

    def absorb_mention(
        self,
        *,
        content: str,
        known_entities: list[str],
        extracted_features: dict[str, object] | None = None,
        high_threshold: float = 0.60,
        weak_threshold: float = 0.35,
    ) -> dict[str, Any]:
        """v1 compatibility shim — buffers the message and returns a hit payload."""
        # 无法确定 user_id，使用 "unknown"
        self.buffer_message(user_id="unknown", content=content)
        return {"level": "new", "score": 0.0, "entry": None, "candidates": []}

    # ── serialization ──────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "entries": [e.to_dict() for e in self.entries],
            "buffer": {
                uid: list(msgs)
                for uid, msgs in self._buffer.items()
                if msgs
            },
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> EventMemoryManager:
        version = payload.get("version", 1)
        if version < 2:
            return cls._migrate_v1(payload)
        manager = cls()
        for item in payload.get("entries", []):
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id", "")).strip()
            if not event_id:
                continue
            from sirius_chat.memory.event.models import EventMemoryEntry as _EME
            manager.entries.append(_EME.from_dict(item))
        for uid, msgs in payload.get("buffer", {}).items():
            if isinstance(msgs, list):
                manager._buffer[uid] = [str(m) for m in msgs]
        return manager

    @classmethod
    def _migrate_v1(cls, payload: dict[str, Any]) -> EventMemoryManager:
        """Migrate v1 events.json (keyword-based entries) → v2 observations."""
        manager = cls()
        for item in payload.get("entries", []):
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id", "")).strip()
            summary = str(item.get("summary", "")).strip()
            if not event_id or not summary:
                continue
            manager.entries.append(EventMemoryEntry(
                event_id=event_id,
                user_id="",           # v1 had no user_id
                category="custom",    # cannot infer from v1
                summary=summary,
                confidence=0.7 if item.get("verified") else 0.4,
                evidence_samples=list(item.get("evidence_samples", [])),
                created_at=str(item.get("created_at", "")),
                updated_at=str(item.get("updated_at", "")),
                mention_count=int(item.get("mention_count", 0)),
                verified=bool(item.get("verified", False)),
            ))
        logger.info("事件记忆 v1→v2 迁移完成，共迁移 %d 条记录", len(manager.entries))
        return manager

    # ── consolidation ──────────────────────────────────────────

    async def consolidate_entries(
        self,
        *,
        user_id: str,
        provider_async: Any,
        model_name: str,
        min_entries: int = 6,
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> int:
        """Consolidate observations for a user into fewer, more refined entries.

        Groups observations by category, uses LLM to merge and summarize them,
        then replaces old entries with consolidated ones.

        Returns the number of entries removed (net reduction).
        """
        from sirius_chat.providers.base import GenerationRequest

        user_entries = [e for e in self.entries if e.user_id == user_id]
        if len(user_entries) < min_entries:
            return 0

        # Group by category
        by_category: dict[str, list[EventMemoryEntry]] = {}
        for entry in user_entries:
            by_category.setdefault(entry.category, []).append(entry)

        total_removed = 0
        now_iso = datetime.now().isoformat(timespec="seconds")

        for category, entries in by_category.items():
            if len(entries) < 3:
                continue

            entries_json = [
                {"summary": e.summary, "confidence": e.confidence, "mention_count": e.mention_count}
                for e in entries
            ]

            system_prompt = (
                "你是记忆归纳器。请将以下同类别的观察记录归纳合并为更少、更精炼的条目。\n"
                "规则：\n"
                "- 合并含义相似或重复的观察\n"
                "- 保留关键细节，去除冗余\n"
                "- 每条归纳结果不超过50字\n"
                "- confidence 取合并条目中的最高值\n"
                "- mention_count 取合并条目的总和\n"
                "严格输出 JSON 数组，每个元素包含：summary(string), confidence(float), mention_count(int)"
            )
            user_prompt = (
                f"类别: {category}\n"
                f"观察列表:\n{json.dumps(entries_json, ensure_ascii=False, indent=2)}"
            )

            request = GenerationRequest(
                model=model_name,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                purpose="event_consolidation",
            )

            try:
                raw = await provider_async.generate_async(request)
            except Exception as exc:
                logger.warning("事件归纳 LLM 调用失败 (user=%s, cat=%s): %s", user_id, category, exc)
                continue

            parsed = self._parse_extraction_response(raw)
            if not parsed:
                continue

            # Remove old entries for this category
            old_ids = {e.event_id for e in entries}
            self.entries = [e for e in self.entries if e.event_id not in old_ids]

            # Add consolidated entries
            for item in parsed:
                summary = str(item.get("summary", "")).strip()[:100]
                if not summary:
                    continue
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
                mention_count = max(1, int(item.get("mention_count", 1)))

                new_entry = EventMemoryEntry(
                    event_id=self._next_event_id(),
                    user_id=user_id,
                    category=category,
                    summary=summary,
                    confidence=confidence,
                    evidence_samples=[],
                    created_at=now_iso,
                    updated_at=now_iso,
                    mention_count=mention_count,
                    verified=True,
                )
                self.entries.append(new_entry)

            total_removed += len(entries) - len(parsed)

        if total_removed > 0:
            logger.info("事件归纳完成 | user=%s | 净减少=%d条", user_id, total_removed)
        return total_removed

    def get_all_user_ids(self) -> set[str]:
        """Return all unique user IDs present in entries."""
        return {e.user_id for e in self.entries if e.user_id}

    # ── parsing helpers ────────────────────────────────────────

    @staticmethod
    def _parse_extraction_response(raw: str) -> list[dict[str, Any]]:
        """Parse LLM JSON array response, tolerating markdown fences."""
        text = raw.strip()
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
        try:
            result = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("观察提取响应 JSON 解析失败")
            return []
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []
