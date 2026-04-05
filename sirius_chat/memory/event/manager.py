"""Event memory manager implementation"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import logging
import re

from sirius_chat.memory.event.models import ContextualEventInterpretation, EventMemoryEntry

logger = logging.getLogger(__name__)


class EventMemoryManager:
    """Manages event memory entries, clustering, and verification."""
    
    def __init__(self):
        self.entries: list[EventMemoryEntry] = []

    _STOPWORDS = {
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "然后",
        "就是",
        "因为",
        "所以",
        "今天",
        "昨天",
        "刚才",
        "现在",
        "一下",
    }

    _ROLE_KEYWORDS = {
        "老板": "manager",
        "领导": "manager",
        "同事": "peer",
        "客户": "client",
        "用户": "user",
        "财务": "finance",
        "运维": "ops",
        "研发": "engineering",
        "产品": "product",
        "测试": "qa",
    }

    _TIME_KEYWORDS = {
        "昨天": "yesterday",
        "今天": "today",
        "上周": "last_week",
        "本周": "this_week",
        "下周": "next_week",
        "月底": "month_end",
        "上线前": "before_release",
        "发布前": "before_release",
        "发布后": "after_release",
    }

    _EMOTION_KEYWORDS = {
        "焦虑": "anxiety",
        "担心": "worry",
        "害怕": "fear",
        "生气": "anger",
        "高兴": "positive",
        "开心": "positive",
        "难过": "sadness",
    }

    @staticmethod
    def _jaccard(left: list[str], right: list[str]) -> float:
        """Calculate Jaccard similarity between two lists."""
        left_set = set(item for item in left if item)
        right_set = set(item for item in right if item)
        if not left_set or not right_set:
            return 0.0
        overlap = len(left_set & right_set)
        union = len(left_set | right_set)
        return overlap / union if union else 0.0

    def _next_event_id(self) -> str:
        """Generate next event ID."""
        return f"evt_{len(self.entries) + 1:04d}"

    def _extract_keywords(self, content: str, max_items: int = 10) -> list[str]:
        """Extract keywords from content."""
        tokens = re.findall(r"[A-Za-z0-9]{2,}", content)
        chinese_only = re.sub(r"[^\u4e00-\u9fff]", "", content)
        for size in (2, 3):
            if len(chinese_only) < size:
                continue
            for index in range(0, len(chinese_only) - size + 1):
                tokens.append(chinese_only[index : index + size])
        normalized: list[str] = []
        for token in tokens:
            value = token.strip().lower()
            if not value or value in self._STOPWORDS:
                continue
            if value not in normalized:
                normalized.append(value)
            if len(normalized) >= max_items:
                break
        return normalized

    def _extract_role_slots(self, content: str) -> list[str]:
        """Extract role slots from content."""
        values: list[str] = []
        for keyword, slot in self._ROLE_KEYWORDS.items():
            if keyword in content and slot not in values:
                values.append(slot)
        return values

    def _extract_time_hints(self, content: str) -> list[str]:
        """Extract time hints from content."""
        values: list[str] = []
        for keyword, tag in self._TIME_KEYWORDS.items():
            if keyword in content and tag not in values:
                values.append(tag)
        return values

    def _extract_emotion_tags(self, content: str) -> list[str]:
        """Extract emotion tags from content."""
        values: list[str] = []
        for keyword, tag in self._EMOTION_KEYWORDS.items():
            if keyword in content and tag not in values:
                values.append(tag)
        return values

    def _extract_entities(self, content: str, known_entities: list[str]) -> list[str]:
        """Extract entities from content."""
        values: list[str] = []
        for entity in known_entities:
            item = entity.strip()
            if item and item in content and item not in values:
                values.append(item)
        return values

    def _build_feature_payload(self, content: str, known_entities: list[str]) -> dict[str, list[str] | str]:
        """Build feature payload from content."""
        summary = content.strip()
        if len(summary) > 72:
            summary = f"{summary[:72]}..."
        return {
            "summary": summary,
            "keywords": self._extract_keywords(content),
            "role_slots": self._extract_role_slots(content),
            "entities": self._extract_entities(content, known_entities),
            "time_hints": self._extract_time_hints(content),
            "emotion_tags": self._extract_emotion_tags(content),
        }

    @staticmethod
    def _normalize_feature_items(value: object) -> list[str]:
        """Normalize feature items."""
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _merge_feature_payload(
        self,
        *,
        base: dict[str, list[str] | str],
        extracted: dict[str, object] | None,
    ) -> dict[str, list[str] | str]:
        """Merge feature payloads."""
        if extracted is None:
            return base
        merged = dict(base)
        summary = str(extracted.get("summary", "")).strip()
        if summary:
            merged["summary"] = summary
        for key in ("keywords", "role_slots", "entities", "time_hints", "emotion_tags"):
            existing = list(merged.get(key, []))
            incoming = self._normalize_feature_items(extracted.get(key))
            merged[key] = self._merge_unique(existing, incoming, 24)
        return merged

    def _score(self, entry: EventMemoryEntry, features: dict[str, list[str] | str]) -> float:
        """Score an entry against features."""
        incoming_summary = str(features.get("summary", ""))
        incoming_keywords = list(features.get("keywords", []))
        incoming_roles = list(features.get("role_slots", []))
        incoming_entities = list(features.get("entities", []))
        incoming_time = list(features.get("time_hints", []))
        incoming_emotion = list(features.get("emotion_tags", []))

        semantic_current = self._extract_keywords(entry.summary + " " + " ".join(entry.evidence_samples), max_items=16)
        semantic_incoming = self._extract_keywords(incoming_summary + " " + " ".join(incoming_keywords), max_items=16)
        semantic_score = self._jaccard(semantic_current, semantic_incoming)
        keyword_score = self._jaccard(entry.keywords, incoming_keywords)
        role_score = self._jaccard(entry.role_slots, incoming_roles)
        time_score = self._jaccard(entry.time_hints, incoming_time)
        entity_score = self._jaccard(entry.entities, incoming_entities)
        emotion_score = self._jaccard(entry.emotion_tags, incoming_emotion)

        score = (
            0.35 * semantic_score
            + 0.20 * keyword_score
            + 0.15 * role_score
            + 0.15 * time_score
            + 0.10 * entity_score
            + 0.05 * emotion_score
        )
        return max(0.0, min(1.0, score))

    @staticmethod
    def _merge_unique(target: list[str], source: list[str], max_items: int) -> list[str]:
        """Merge unique items from source into target."""
        values = list(target)
        for item in source:
            if item and item not in values:
                values.append(item)
        if len(values) > max_items:
            values = values[-max_items:]
        return values

    def _update_entry(
        self,
        entry: EventMemoryEntry,
        *,
        features: dict[str, list[str] | str],
        content: str,
    ) -> None:
        """Update entry with new features."""
        summary = str(features.get("summary", "")).strip()
        if summary:
            entry.summary = summary
        entry.keywords = self._merge_unique(entry.keywords, list(features.get("keywords", [])), 20)
        entry.role_slots = self._merge_unique(entry.role_slots, list(features.get("role_slots", [])), 12)
        entry.entities = self._merge_unique(entry.entities, list(features.get("entities", [])), 16)
        entry.time_hints = self._merge_unique(entry.time_hints, list(features.get("time_hints", [])), 12)
        entry.emotion_tags = self._merge_unique(entry.emotion_tags, list(features.get("emotion_tags", [])), 12)

        sample = content.strip()
        if len(sample) > 96:
            sample = f"{sample[:96]}..."
        if sample:
            entry.evidence_samples = self._merge_unique(entry.evidence_samples, [sample], 6)

        now_text = datetime.now().isoformat(timespec="seconds")
        if not entry.created_at:
            entry.created_at = now_text
        entry.updated_at = now_text
        entry.hit_count += 1
        entry.mention_count += 1

    def absorb_mention(
        self,
        *,
        content: str,
        known_entities: list[str],
        extracted_features: dict[str, object] | None = None,
        high_threshold: float = 0.60,
        weak_threshold: float = 0.35,
    ) -> dict[str, Any]:
        """Absorb a mention of an event, clustering with existing if similar."""
        base = self._build_feature_payload(content, known_entities)
        features = self._merge_feature_payload(base=base, extracted=extracted_features)

        if not self.entries:
            entry = EventMemoryEntry(event_id=self._next_event_id(), summary=str(features["summary"]))
            self._update_entry(entry, features=features, content=content)
            self.entries.append(entry)
            return {"level": "new", "entry": entry, "score": 0.0, "candidates": []}

        scored: list[tuple[float, EventMemoryEntry]] = []
        for entry in self.entries:
            scored.append((self._score(entry, features), entry))
        scored.sort(key=lambda item: item[0], reverse=True)

        best_score, best_entry = scored[0]
        incoming_keywords = list(features.get("keywords", []))
        incoming_roles = list(features.get("role_slots", []))
        keyword_overlap = len(set(best_entry.keywords) & set(incoming_keywords))
        role_overlap = len(set(best_entry.role_slots) & set(incoming_roles))
        fallback_weak_hit = keyword_overlap >= 2 or (keyword_overlap >= 1 and role_overlap >= 1)
        if best_score >= high_threshold:
            self._update_entry(best_entry, features=features, content=content)
            return {
                "level": "high",
                "entry": best_entry,
                "score": best_score,
                "candidates": [item[1].event_id for item in scored[:3]],
            }

        if best_score >= weak_threshold or fallback_weak_hit:
            self._update_entry(best_entry, features=features, content=content)
            return {
                "level": "weak",
                "entry": best_entry,
                "score": best_score,
                "candidates": [item[1].event_id for item in scored[:3]],
            }

        entry = EventMemoryEntry(event_id=self._next_event_id(), summary=str(features["summary"]))
        self._update_entry(entry, features=features, content=content)
        self.entries.append(entry)
        return {
            "level": "new",
            "entry": entry,
            "score": best_score,
            "candidates": [item[1].event_id for item in scored[:3]],
        }

    def top_events(self, limit: int = 5, include_pending: bool = False) -> list[EventMemoryEntry]:
        """Get top events.
        
        Args:
            limit: Maximum number of events to return
            include_pending: If False (default), only return verified events.
                           If True, include pending events with mention_count >= 2.
        
        Returns:
            List of top events sorted by recency and hit count
        """
        if include_pending:
            # Include both verified and recent pending events
            filtered = [
                e for e in self.entries
                if e.verified or e.mention_count >= 2
            ]
        else:
            # Only include verified events
            filtered = [e for e in self.entries if e.verified]
        
        values = sorted(filtered, key=lambda item: (item.updated_at, item.hit_count), reverse=True)
        return values[:limit]

    async def finalize_pending_events(
        self,
        provider_async: Any,  # AsyncLLMProvider
        model_name: str,
        min_mentions: int = 3,
    ) -> dict[str, Any]:
        """Verify pending events using LLM.
        
        Args:
            provider_async: AsyncLLMProvider to call for LLM verification
            model_name: Model to use for verification
            min_mentions: Minimum mention count to qualify for verification
            
        Returns:
            Dictionary with:
            - verified_count: Number of newly verified events
            - rejected_count: Number of rejected events
            - pending_count: Number of remaining pending events
        """
        from sirius_chat.providers.base import GenerationRequest
        
        # Find pending events that meet the threshold
        pending = [e for e in self.entries if not e.verified and e.mention_count >= min_mentions]
        
        verified_count = 0
        rejected_count = 0
        
        for entry in pending:
            # Prepare verification prompt
            prompt = f"""Analyze potential events in the conversation and judge whether they should be recorded.

Event Summary: {entry.summary}
Related Keywords: {", ".join(entry.keywords) if entry.keywords else "None"}
Mentioned Roles: {", ".join(entry.role_slots) if entry.role_slots else "None"}
Evidence Samples (Total {len(entry.evidence_samples)} mentions):
{chr(10).join(f"- {s}" for s in entry.evidence_samples)}

Based on the above analysis, answer:
1. Is this event worth recording? (Yes/No)
2. If yes, provide an improved summary (1-2 sentences)
3. If yes, provide 5-10 keywords related to this event
4. If yes, which people/roles are involved? (e.g.: manager, colleague, client)
5. If yes, are there timeline clues? (e.g.: yesterday, this week, next month)
6. If yes, what emotions are expressed? (e.g.: anxiety, positive)

Please answer in JSON format:
{{
  "record": "Yes" or "No",
  "reason": "Brief reason",
  "summary": "Improved summary (if yes)",
  "keywords": ["keyword1", "keyword2", ...],
  "role_slots": ["manager", "colleague", ...],
  "time_hints": ["yesterday", ...],
  "emotion_tags": ["anxiety", ...]
}}"""

            request = GenerationRequest(
                model=model_name,
                system_prompt="You are a dialogue analysis expert skilled at extracting meaningful event information from conversations.",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=512,
            )
            
            try:
                response = await provider_async.generate_async(request)
                
                # Parse JSON response
                import json
                # Extract JSON from response (may be wrapped in markdown code blocks)
                json_str = response
                if "```" in response:
                    json_str = response.split("```")[1]
                    if json_str.startswith("json"):
                        json_str = json_str[4:]
                
                result = json.loads(json_str.strip())
                
                record_value = result.get("record", "").lower().strip()
                # Support both Chinese and English judgments
                if record_value in ("yes", "是", "y", "✓"):
                    # Update event based on LLM feedback
                    entry.verified = True
                    summary = result.get("summary", "").strip()
                    if summary:
                        entry.summary = summary
                    
                    # Merge LLM-extracted features
                    if "keywords" in result:
                        entry.keywords = self._merge_unique(
                            entry.keywords,
                            result.get("keywords", []),
                            20
                        )
                    if "role_slots" in result:
                        entry.role_slots = self._merge_unique(
                            entry.role_slots,
                            result.get("role_slots", []),
                            12
                        )
                    if "time_hints" in result:
                        entry.time_hints = self._merge_unique(
                            entry.time_hints,
                            result.get("time_hints", []),
                            12
                        )
                    if "emotion_tags" in result:
                        entry.emotion_tags = self._merge_unique(
                            entry.emotion_tags,
                            result.get("emotion_tags", []),
                            12
                        )
                    
                    entry.updated_at = datetime.now().isoformat(timespec="seconds")
                    verified_count += 1
                else:
                    # Delete events LLM says are not worth recording
                    self.entries.remove(entry)
                    rejected_count += 1
                    
            except Exception as e:
                # Log error but continue processing other events
                logger.warning(f"Event verification failed {entry.event_id}: {e}")
        
        pending_after = [e for e in self.entries if not e.verified and e.mention_count >= min_mentions]
        
        return {
            "verified_count": verified_count,
            "rejected_count": rejected_count,
            "pending_count": len(pending_after),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "entries": [
                {
                    "event_id": item.event_id,
                    "summary": item.summary,
                    "keywords": item.keywords,
                    "role_slots": item.role_slots,
                    "entities": item.entities,
                    "time_hints": item.time_hints,
                    "emotion_tags": item.emotion_tags,
                    "evidence_samples": item.evidence_samples,
                    "hit_count": item.hit_count,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "verified": item.verified,
                    "mention_count": item.mention_count,
                }
                for item in self.entries
            ]
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EventMemoryManager":
        """Deserialize from dictionary."""
        manager = cls()
        raw = payload.get("entries", [])
        if not isinstance(raw, list):
            return manager
        for item in raw:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("event_id", "")).strip()
            summary = str(item.get("summary", "")).strip()
            if not event_id or not summary:
                continue
            manager.entries.append(
                EventMemoryEntry(
                    event_id=event_id,
                    summary=summary,
                    keywords=list(item.get("keywords", [])),
                    role_slots=list(item.get("role_slots", [])),
                    entities=list(item.get("entities", [])),
                    time_hints=list(item.get("time_hints", [])),
                    emotion_tags=list(item.get("emotion_tags", [])),
                    evidence_samples=list(item.get("evidence_samples", [])),
                    hit_count=int(item.get("hit_count", 0)),
                    created_at=str(item.get("created_at", "")),
                    updated_at=str(item.get("updated_at", "")),
                    verified=bool(item.get("verified", False)),
                    mention_count=int(item.get("mention_count", 0)),
                )
            )
        return manager
