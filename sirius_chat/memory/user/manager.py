"""User memory manager implementation"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.user.models import MemoryFact, UserMemoryEntry, UserProfile, UserRuntimeState
from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY

logger = logging.getLogger(__name__)

# ============================================================================
# Performance optimization constants
# ============================================================================

# C1: Memory Facts upper limit management
MAX_MEMORY_FACTS = 50  # Maximum memory facts per user

# A1: Time window deduplication (minutes)
EVENT_DEDUP_WINDOW_MINUTES = 5


class UserMemoryManager:
    """Manages user memory entries, facts, and profiles."""
    
    def __init__(self):
        self.entries: dict[str, UserMemoryEntry] = {}
        self.speaker_index: dict[str, str] = {}
        self.identity_index: dict[str, str] = {}

    @staticmethod
    def _normalize_label(label: str) -> str:
        return label.strip().lower()

    @staticmethod
    def _identity_key(channel: str, external_user_id: str) -> str:
        return f"{channel.strip().lower()}:{external_user_id.strip().lower()}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _normalize_summary_note(note: str) -> str:
        value = note.strip().lower()
        prefixes = ("事件摘要：", "多模态证据：")
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
        return value

    def _append_summary_note(self, *, entry: UserMemoryEntry, note: str, max_notes: int = 8) -> bool:
        value = note.strip()
        if not value:
            return False
        normalized = self._normalize_summary_note(value)
        for existing in entry.runtime.summary_notes:
            if self._normalize_summary_note(existing) == normalized:
                return False
        entry.runtime.summary_notes.append(value)
        if len(entry.runtime.summary_notes) > max_notes:
            entry.runtime.summary_notes = entry.runtime.summary_notes[-max_notes:]
        return True

    def _normalize_trait(self, trait: str) -> str:
        """B approach: Normalize trait to classification label or preserve original.
        
        If trait matches defined categories (keyword matching), return category label.
        Otherwise return original trait to avoid over-normalization.
        
        Matching strategies:
        1. Check if already a classification label (case-insensitive)
        2. Try exact word matching (complete equality)
        3. Finally try substring partial matching
        """
        if not trait or not isinstance(trait, str):
            return ""
        
        trait_stripped = trait.strip().lower()
        if not trait_stripped:
            return ""
        
        # Check if already a classification label (case-insensitive)
        for category in TRAIT_TAXONOMY:
            if category.lower() == trait_stripped:
                return category
        
        # Strategy 1: Exact word matching (avoid substring conflict e.g. "work" vs "work-life balance")
        for category, info in TRAIT_TAXONOMY.items():
            keywords = info.get("keywords", [])
            if any(kw.lower() == trait_stripped for kw in keywords):
                return category
        
        # Strategy 2: Substring matching (for compound words)
        for category, info in TRAIT_TAXONOMY.items():
            keywords = info.get("keywords", [])
            if any(kw.lower() in trait_stripped or trait_stripped in kw.lower() for kw in keywords):
                return category
        
        return trait  # Cannot categorize, preserve original

    def add_memory_fact(
        self,
        *,
        user_id: str,
        fact_type: str,
        value: str,
        source: str,
        confidence: float,
        observed_at: str | None = None,
        max_facts: int | None = None,
    ) -> None:
        """Add memory fact with trait normalization and intelligent upper limit management.
        
        C1 approach: When exceeding max_facts, delete lowest-confidence facts rather than simple FIFO.
        B approach: Auto-apply trait normalization for certain fact_types.
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return
        
        # Use global constant as default limit
        if max_facts is None:
            max_facts = MAX_MEMORY_FACTS
        
        text = value.strip()
        if not text:
            return
        
        # B approach: Normalize traits
        if fact_type in ("trait", "inferred_trait", "preference_tag"):
            normalized_trait = self._normalize_trait(text)
            if normalized_trait:
                text = normalized_trait
        
        timestamp = observed_at or self._now_iso()
        normalized = self._normalize_summary_note(text)
        
        # Check if similar fact exists, update confidence if found
        for item in entry.runtime.memory_facts:
            if item.fact_type != fact_type:
                continue
            if self._normalize_summary_note(item.value) != normalized:
                continue
            if confidence > item.confidence:
                item.confidence = confidence
                item.source = source
                item.observed_at = timestamp
            return
        
        # Add new fact
        final_confidence = max(0.0, min(1.0, float(confidence)))
        # C2 approach: Auto-mark is_transient and created_at
        is_transient_fact = final_confidence <= 0.85
        created_at_time = timestamp if is_transient_fact else ""
        
        entry.runtime.memory_facts.append(
            MemoryFact(
                fact_type=fact_type,
                value=text,
                source=source,
                confidence=final_confidence,
                observed_at=timestamp,
                is_transient=is_transient_fact,
                created_at=created_at_time,
            )
        )
        
        # C1 approach: Smart cleanup - when exceeding limit, delete lowest-confidence facts
        if len(entry.runtime.memory_facts) > max_facts:
            # Sort by confidence ascending
            sorted_facts = sorted(
                entry.runtime.memory_facts,
                key=lambda f: f.confidence
            )
            # Calculate deletion count (delete bottom 10%, minimum 1)
            num_to_delete = max(1, len(entry.runtime.memory_facts) // 10)
            # Keep top 90%
            entry.runtime.memory_facts = sorted_facts[num_to_delete:]

    def register_user(self, profile: UserProfile) -> None:
        """Register a user profile."""
        if not profile.user_id:
            profile.user_id = profile.name
        if profile.user_id not in self.entries:
            self.entries[profile.user_id] = UserMemoryEntry(profile=profile)
        else:
            existing = self.entries[profile.user_id].profile
            if profile.name and not existing.name:
                existing.name = profile.name
            if profile.persona and not existing.persona:
                existing.persona = profile.persona
            for channel, external_id in profile.identities.items():
                if channel and external_id:
                    existing.identities[channel] = external_id
            for alias in profile.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            for trait in profile.traits:
                if trait not in existing.traits:
                    existing.traits.append(trait)
            existing.metadata.update(profile.metadata)

        labels = [profile.user_id, profile.name, *profile.aliases]
        for label in labels:
            if not label:
                continue
            self.speaker_index[self._normalize_label(label)] = profile.user_id

        for channel, external_id in profile.identities.items():
            if not channel or not external_id:
                continue
            self.identity_index[self._identity_key(channel, external_id)] = profile.user_id

    def resolve_user_id(
        self,
        *,
        speaker: str | None = None,
        channel: str | None = None,
        external_user_id: str | None = None,
    ) -> str | None:
        """Resolve user ID from speaker name, channel identity, or external user ID."""
        if channel and external_user_id:
            identity_user_id = self.identity_index.get(self._identity_key(channel, external_user_id))
            if identity_user_id:
                return identity_user_id
        if speaker:
            return self.speaker_index.get(self._normalize_label(speaker))
        return None

    def resolve_user_id_by_identity(self, *, channel: str, external_user_id: str) -> str | None:
        """Resolve user ID by channel identity."""
        return self.identity_index.get(self._identity_key(channel, external_user_id))

    def get_user_by_identity(self, *, channel: str, external_user_id: str) -> UserMemoryEntry | None:
        """Get user memory entry by channel identity."""
        user_id = self.resolve_user_id_by_identity(channel=channel, external_user_id=external_user_id)
        if not user_id:
            return None
        return self.entries.get(user_id)

    def ensure_user(self, *, speaker: str, persona: str = "") -> UserProfile:
        """Ensure user exists, creating if necessary."""
        resolved_user_id = self.resolve_user_id(speaker=speaker)
        if resolved_user_id and resolved_user_id in self.entries:
            entry = self.entries[resolved_user_id]
            if persona and not entry.profile.persona:
                entry.profile.persona = persona
            return entry.profile

        profile = UserProfile(user_id=speaker, name=speaker, persona=persona)
        self.register_user(profile)
        return profile

    def remember_message(
        self,
        *,
        profile: UserProfile,
        content: str,
        max_recent_messages: int,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> None:
        """Remember a message from user."""
        self.register_user(profile)
        entry = self.entries[profile.user_id]
        entry.runtime.recent_messages.append(content)
        if len(entry.runtime.recent_messages) > max_recent_messages:
            entry.runtime.recent_messages = entry.runtime.recent_messages[-max_recent_messages:]
        if channel:
            entry.runtime.last_seen_channel = channel
        if channel_user_id:
            entry.runtime.last_seen_uid = channel_user_id

    def apply_ai_runtime_update(
        self,
        *,
        user_id: str,
        inferred_persona: str | None = None,
        inferred_aliases: list[str] | None = None,
        inferred_traits: list[str] | None = None,
        preference_tags: list[str] | None = None,
        summary_note: str | None = None,
        source: str = "unknown",
        confidence: float = 0.5,
    ) -> None:
        """Apply AI inferred runtime updates to user memory."""
        entry = self.entries.get(user_id)
        if entry is None:
            return
        if inferred_persona:
            entry.runtime.inferred_persona = inferred_persona
        if inferred_aliases:
            for alias in inferred_aliases:
                value = alias.strip()
                if not value:
                    continue
                if value not in entry.profile.aliases:
                    entry.profile.aliases.append(value)
                self.speaker_index[self._normalize_label(value)] = user_id
        if inferred_traits:
            for item in inferred_traits:
                if item not in entry.runtime.inferred_traits:
                    entry.runtime.inferred_traits.append(item)
        if preference_tags:
            for item in preference_tags:
                if item not in entry.runtime.preference_tags:
                    entry.runtime.preference_tags.append(item)
        if summary_note:
            appended = self._append_summary_note(entry=entry, note=summary_note, max_notes=8)
            if appended:
                self.add_memory_fact(
                    user_id=user_id,
                    fact_type="summary",
                    value=summary_note,
                    source=source,
                    confidence=confidence,
                )

    def add_summary_note(self, *, user_id: str, note: str, max_notes: int = 8) -> None:
        """Add a summary note for user."""
        entry = self.entries.get(user_id)
        if entry is None:
            return
        appended = self._append_summary_note(entry=entry, note=note, max_notes=max_notes)
        if appended:
            self.add_memory_fact(
                user_id=user_id,
                fact_type="summary",
                value=note,
                source="manual",
                confidence=0.9,
            )

    def merge_from(self, other: "UserMemoryManager") -> None:
        """Merge another UserMemoryManager into this one."""
        for user_id, incoming in other.entries.items():
            incoming_profile = UserProfile(
                user_id=incoming.profile.user_id,
                name=incoming.profile.name,
                persona=incoming.profile.persona,
                identities=dict(incoming.profile.identities),
                aliases=list(incoming.profile.aliases),
                traits=list(incoming.profile.traits),
                metadata=dict(incoming.profile.metadata),
            )
            self.register_user(incoming_profile)
            current = self.entries[user_id]

            if incoming.runtime.inferred_persona and not current.runtime.inferred_persona:
                current.runtime.inferred_persona = incoming.runtime.inferred_persona

            for trait in incoming.runtime.inferred_traits:
                if trait not in current.runtime.inferred_traits:
                    current.runtime.inferred_traits.append(trait)

            for tag in incoming.runtime.preference_tags:
                if tag not in current.runtime.preference_tags:
                    current.runtime.preference_tags.append(tag)

            for msg in incoming.runtime.recent_messages:
                if msg not in current.runtime.recent_messages:
                    current.runtime.recent_messages.append(msg)
            if len(current.runtime.recent_messages) > 8:
                current.runtime.recent_messages = current.runtime.recent_messages[-8:]

            for note in incoming.runtime.summary_notes:
                self._append_summary_note(entry=current, note=note, max_notes=8)

            for fact in incoming.runtime.memory_facts:
                self.add_memory_fact(
                    user_id=user_id,
                    fact_type=fact.fact_type,
                    value=fact.value,
                    source=fact.source,
                    confidence=fact.confidence,
                    observed_at=fact.observed_at,
                )

            if incoming.runtime.last_seen_channel and not current.runtime.last_seen_channel:
                current.runtime.last_seen_channel = incoming.runtime.last_seen_channel
            if incoming.runtime.last_seen_uid and not current.runtime.last_seen_uid:
                current.runtime.last_seen_uid = incoming.runtime.last_seen_uid

    def apply_scheduled_decay(self) -> dict[str, int]:
        """Apply scheduled decay to all user memories.
        
        Returns: {user_id: number of decayed memories}
        """
        from sirius_chat.memory.quality.models import MemoryForgetEngine
        return MemoryForgetEngine.apply_scheduled_decay(self)
    
    def cleanup_expired_memories(self, min_quality: float = 0.25) -> dict[str, int]:
        """Clean up expired/low-quality memories for all users.
        
        Returns: {user_id: number of deleted memories}
        """
        from sirius_chat.memory.quality.models import MemoryForgetEngine
        cleanup_stats = {}
        for user_id, entry in self.entries.items():
            deleted_count = MemoryForgetEngine.cleanup_user_memories(entry, min_quality=min_quality)
            if deleted_count > 0:
                cleanup_stats[user_id] = deleted_count
        return cleanup_stats

    def apply_event_insights(
        self,
        user_id: str,
        event_features: dict[str, object],
        source: str = "event_extract",
        base_confidence: float = 0.65,
    ) -> None:
        """Convert event features to user memory facts and feature signals.
        
        Auto-converts event's emotion_tags, keywords, role_slots, time_hints etc.
        to corresponding user memory facts and updates observed feature sets.
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return

        # 1. Emotion recognition → user feature signals and memory facts
        emotions = event_features.get("emotion_tags", [])
        if isinstance(emotions, list) and emotions:
            clean_emotions = [str(e).strip() for e in emotions if str(e).strip()]
            entry.runtime.observed_emotions.update(clean_emotions)
            
            emotion_str = ", ".join(clean_emotions[:3])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="emotional_pattern",
                value=f"Expressed emotions: {emotion_str}",
                source=source,
                confidence=base_confidence - 0.05,
            )

        # 2. Keyword accumulation → user interests and memory facts
        keywords = event_features.get("keywords", [])
        if isinstance(keywords, list) and keywords:
            clean_keywords = [str(k).strip() for k in keywords if str(k).strip()]
            entry.runtime.observed_keywords.update(clean_keywords)
            
            # Take top 5 keywords to avoid length
            keywords_str = ", ".join(clean_keywords[:5])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="user_interest",
                value=f"Topics of interest: {keywords_str}",
                source=source,
                confidence=base_confidence - 0.1,
            )

        # 3. Role recognition → social network and feature lifting
        roles = event_features.get("role_slots", [])
        if isinstance(roles, list) and roles:
            clean_roles = [str(r).strip() for r in roles if str(r).strip()]
            entry.runtime.observed_roles.update(clean_roles)
            
            roles_str = ", ".join(set(clean_roles))
            self.add_memory_fact(
                user_id=user_id,
                fact_type="social_context",
                value=f"Interacts with roles: {roles_str}",
                source=source,
                confidence=base_confidence - 0.05,
            )
            
            # Feature lifting: detect leadership-related roles
            leadership_roles = {"manager", "leader", "lead", "主导", "负责人"}
            if any(role in leadership_roles for role in clean_roles):
                if "leadership_tendency" not in entry.runtime.inferred_traits:
                    entry.runtime.inferred_traits.append("leadership_tendency")

        # 4. Entity recognition → known entity set
        entities = event_features.get("entities", [])
        if isinstance(entities, list) and entities:
            clean_entities = [str(e).strip() for e in entities if str(e).strip()]
            entry.runtime.observed_entities.update(clean_entities)

    def get_resident_facts(self, user_id: str) -> list[MemoryFact]:
        """Get high-confidence RESIDENT facts (only for persistence to user.json).
        
        RESIDENT: confidence > 0.85, representing core, stable user traits and preferences.
        These facts should be persisted to storage.
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence > 0.85
        ]

    def get_transient_facts(self, user_id: str) -> list[MemoryFact]:
        """Get low-confidence TRANSIENT facts (stored in session memory).
        
        TRANSIENT: confidence ≤ 0.85, representing recently observed uncertain information.
        These facts should be stored in session memory and auto-cleaned after 30 minutes.
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence <= 0.85
        ]

    def get_user_by_id(self, user_id: str) -> UserMemoryEntry | None:
        """Get user memory entry by exact user ID.
        
        Args:
            user_id: The user ID to look up
        
        Returns:
            UserMemoryEntry or None if not found
        """
        return self.entries.get(user_id)

    def search_users_by_fact(
        self, 
        fact_type: str, 
        value: str | None = None,
    ) -> dict[str, list[MemoryFact]]:
        """Search for users with specific fact types or values.
        
        Args:
            fact_type: The type of fact to search for (e.g., "job", "location", "hobby")
            value: Optional specific value to match. If None, returns all facts of that type.
        
        Returns:
            Dict mapping user_id to list of matching MemoryFact objects
        """
        results: dict[str, list[MemoryFact]] = {}
        
        for user_id, entry in self.entries.items():
            matching_facts = []
            for fact in entry.runtime.memory_facts:
                if fact.fact_type != fact_type:
                    continue
                if value is not None and value.lower() not in fact.value.lower():
                    continue
                matching_facts.append(fact)
            
            if matching_facts:
                results[user_id] = matching_facts
        
        return results

    def get_rich_user_summary(
        self, 
        user_id: str, 
        include_transient: bool = True,
    ) -> dict[str, Any]:
        """Generate a model-friendly user summary with rich context.
        
        This summary is suitable for injection into system prompts or as context
        for the AI model to provide personalized responses.
        
        Args:
            user_id: The user ID to generate summary for
            include_transient: Whether to include low-confidence transient facts
        
        Returns:
            Dict with keys: profile, summary, traits, interests, recent_facts, 
                           identities, confidence_distribution, channels
        """
        entry = self.get_user_by_id(user_id)
        if entry is None:
            return {}
        
        profile = entry.profile
        runtime = entry.runtime
        
        # Separate facts by confidence
        resident_facts = self.get_resident_facts(user_id)
        transient_facts = self.get_transient_facts(user_id)
        
        # Build facts list based on include_transient flag
        facts_to_include = resident_facts
        if include_transient:
            facts_to_include.extend(transient_facts)
        
        # Group facts by type for organized summary
        facts_by_type: dict[str, list[dict[str, Any]]] = {}
        for fact in facts_to_include:
            fact_type = fact.fact_type
            if fact_type not in facts_by_type:
                facts_by_type[fact_type] = []
            
            fact_info = {
                "value": fact.value,
                "confidence": fact.confidence,
                "source": fact.source,
                "time_desc": getattr(fact, "observed_time_desc", ""),
                "channel": getattr(fact, "context_channel", ""),
                "topic": getattr(fact, "context_topic", ""),
            }
            # Clean up empty fields
            fact_info = {k: v for k, v in fact_info.items() if v}
            facts_by_type[fact_type].append(fact_info)
        
        # Extract key traits and interests
        key_traits = []
        for trait in runtime.inferred_traits[:5]:  # Top 5 traits
            key_traits.append(trait)
        
        key_interests = []
        for tag in runtime.preference_tags[:5]:  # Top 5 interests
            key_interests.append(tag)
        
        # Identify communication channels
        observed_channels = set()
        for fact in facts_to_include:
            channel = getattr(fact, "context_channel", "")
            if channel:
                observed_channels.add(channel)
        
        if runtime.last_seen_channel:
            observed_channels.add(runtime.last_seen_channel)
        
        return {
            "user_id": user_id,
            "name": profile.name,
            "aliases": profile.aliases[:3],  # Top 3 aliases
            "persona": profile.persona,
            "inferred_persona": runtime.inferred_persona,
            "traits": key_traits,
            "interests": key_interests,
            "recent_summary": runtime.summary_notes[:3] if runtime.summary_notes else [],
            "facts_by_type": facts_by_type,
            "identities": {channel: uid for channel, uid in profile.identities.items()},
            "channels": list(observed_channels),
            "observed_entities": list(runtime.observed_entities)[:10],  # Top 10 entities
            "confidence_stats": {
                "resident_count": len(resident_facts),
                "transient_count": len(transient_facts) if include_transient else 0,
                "avg_confidence": (
                    sum(f.confidence for f in facts_to_include) / len(facts_to_include)
                    if facts_to_include else 0.0
                ),
            },
        }

    def get_facts_by_context(
        self,
        user_id: str,
        channel: str | None = None,
        topic: str | None = None,
    ) -> list[MemoryFact]:
        """Get facts filtered by communication channel and/or topic.
        
        Args:
            user_id: The user ID to query
            channel: Optional channel filter (e.g., "qq", "wechat", "email")
            topic: Optional topic filter (e.g., "work", "hobby", "family")
        
        Returns:
            List of MemoryFact objects matching the filters
        """
        entry = self.get_user_by_id(user_id)
        if entry is None:
            return []
        
        results = []
        for fact in entry.runtime.memory_facts:
            # Check channel match
            if channel is not None:
                fact_channel = getattr(fact, "context_channel", "")
                if fact_channel.lower() != channel.lower():
                    continue
            
            # Check topic match
            if topic is not None:
                fact_topic = getattr(fact, "context_topic", "")
                if fact_topic.lower() != topic.lower():
                    continue
            
            results.append(fact)
        
        return results

    def cleanup_expired_transient_facts(
        self,
        user_id: str,
        max_age_minutes: int = 30,
    ) -> int:
        """Clean up expired TRANSIENT facts.
        
        TRANSIENT facts are deleted after max_age_minutes (default 30) from creation.
        Returns number of deleted facts.
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return 0
        
        now = datetime.now(timezone.utc)
        deleted_count = 0
        facts_to_keep = []
        
        for fact in entry.runtime.memory_facts:
            # Only check transient facts
            if not fact.is_transient:
                facts_to_keep.append(fact)
                continue
            
            # Check expiry
            if fact.created_at:
                try:
                    created_time = datetime.fromisoformat(fact.created_at)
                    age_minutes = (now - created_time).total_seconds() / 60
                    if age_minutes > max_age_minutes:
                        deleted_count += 1
                        continue  # Delete this fact
                except (ValueError, TypeError):
                    # Parse error, keep this fact
                    pass
            
            facts_to_keep.append(fact)
        
        entry.runtime.memory_facts = facts_to_keep
        
        if deleted_count > 0:
            logger.debug(
                f"Cleaned up {deleted_count} expired transient facts for user {user_id}"
            )
        
        return deleted_count

    def compress_memory_facts(
        self,
        user_id: str,
        similarity_threshold: float = 0.8,
    ) -> int:
        """C3 approach: Dynamic memory facts compression.
        
        Cluster and merge same-type facts to reduce redundant information.
        
        Args:
            user_id: User ID to compress
            similarity_threshold: Similarity threshold (0.0-1.0)
        
        Returns:
            Number of compressed/deleted facts
        """
        entry = self.entries.get(user_id)
        if entry is None:
            return 0
        
        facts = entry.runtime.memory_facts
        if len(facts) < 10:  # Too few facts, skip compression
            return 0
        
        # Group by fact_type
        facts_by_type: dict[str, list[MemoryFact]] = {}
        for fact in facts:
            if fact.fact_type not in facts_by_type:
                facts_by_type[fact.fact_type] = []
            facts_by_type[fact.fact_type].append(fact)
        
        original_count = len(facts)
        compressed_facts = []
        
        # Compress facts of each type
        for fact_type, facts_of_type in facts_by_type.items():
            if len(facts_of_type) <= 3:
                # Too few facts, keep all
                compressed_facts.extend(facts_of_type)
                continue
            
            # Strategy: Delete lowest confidence facts, keep top 70%
            sorted_facts = sorted(facts_of_type, key=lambda f: f.confidence, reverse=True)
            keep_count = max(2, int(len(sorted_facts) * 0.7))
            compressed_facts.extend(sorted_facts[:keep_count])
        
        # Re-sort to maintain observed_at order
        compressed_facts.sort(
            key=lambda f: f.observed_at,
            reverse=True
        )
        
        entry.runtime.memory_facts = compressed_facts
        deleted_count = original_count - len(compressed_facts)
        
        if deleted_count > 0:
            logger.info(
                f"Compressed facts for user {user_id}: "
                f"{original_count} → {len(compressed_facts)} ({deleted_count} deleted)"
            )
        
        return deleted_count

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "entries": {
                user_id: {
                    "profile": {
                        "user_id": entry.profile.user_id,
                        "name": entry.profile.name,
                        "persona": entry.profile.persona,
                        "identities": entry.profile.identities,
                        "aliases": entry.profile.aliases,
                        "traits": entry.profile.traits,
                        "metadata": entry.profile.metadata,
                    },
                    "runtime": {
                        "inferred_persona": entry.runtime.inferred_persona,
                        "inferred_traits": entry.runtime.inferred_traits,
                        "preference_tags": entry.runtime.preference_tags,
                        "recent_messages": entry.runtime.recent_messages,
                        "summary_notes": entry.runtime.summary_notes,
                        "memory_facts": [
                            {
                                "fact_type": item.fact_type,
                                "value": item.value,
                                "source": item.source,
                                "confidence": item.confidence,
                                "observed_at": item.observed_at,
                                "memory_category": item.memory_category,
                                "validated": item.validated,
                                "conflict_with": item.conflict_with,
                                # C2: RESIDENT/TRANSIENT marker
                                "is_transient": item.is_transient,
                                "created_at": item.created_at,
                            }
                            for item in entry.runtime.memory_facts
                        ],
                        "last_seen_channel": entry.runtime.last_seen_channel,
                        "last_seen_uid": entry.runtime.last_seen_uid,
                        "observed_keywords": list(entry.runtime.observed_keywords),
                        "observed_roles": list(entry.runtime.observed_roles),
                        "observed_emotions": list(entry.runtime.observed_emotions),
                        "observed_entities": list(entry.runtime.observed_entities),
                        # A1: Serialize timestamp
                        "last_event_processed_at": (
                            entry.runtime.last_event_processed_at.isoformat()
                            if entry.runtime.last_event_processed_at is not None
                            else None
                        ),
                    },
                }
                for user_id, entry in self.entries.items()
            },
            "speaker_index": self.speaker_index,
            "identity_index": self.identity_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserMemoryManager":
        """Deserialize from dictionary."""
        manager = cls()
        raw_entries = payload.get("entries", {})
        for user_id, item in raw_entries.items():
            profile_data = item.get("profile", {})
            profile = UserProfile(
                user_id=profile_data.get("user_id", user_id),
                name=profile_data.get("name", user_id),
                persona=profile_data.get("persona", ""),
                identities=dict(profile_data.get("identities", {})),
                aliases=list(profile_data.get("aliases", [])),
                traits=list(profile_data.get("traits", [])),
                metadata=dict(profile_data.get("metadata", {})),
            )
            runtime_data = item.get("runtime", {})
            if not runtime_data:
                # Backward compatibility for old entry fields.
                runtime_data = {
                    "recent_messages": list(item.get("recent_messages", [])),
                    "summary_notes": list(item.get("summary_notes", [])),
                }
            manager.entries[user_id] = UserMemoryEntry(
                profile=profile,
                runtime=UserRuntimeState(
                    inferred_persona=str(runtime_data.get("inferred_persona", "")),
                    inferred_traits=list(runtime_data.get("inferred_traits", [])),
                    preference_tags=list(runtime_data.get("preference_tags", [])),
                    recent_messages=list(runtime_data.get("recent_messages", [])),
                    summary_notes=list(runtime_data.get("summary_notes", [])),
                    memory_facts=[
                        MemoryFact(
                            fact_type=str(item.get("fact_type", "")).strip() or "summary",
                            value=str(item.get("value", "")).strip(),
                            source=str(item.get("source", "unknown")).strip() or "unknown",
                            confidence=float(item.get("confidence", 0.5)),
                            observed_at=str(item.get("observed_at", "")).strip(),
                            memory_category=str(item.get("memory_category", "custom")).strip() or "custom",
                            validated=bool(item.get("validated", False)),
                            conflict_with=list(item.get("conflict_with", [])),
                            # C2: Deserialize RESIDENT/TRANSIENT marker
                            is_transient=bool(item.get("is_transient", False)),
                            created_at=str(item.get("created_at", "")).strip(),
                        )
                        for item in list(runtime_data.get("memory_facts", []))
                        if isinstance(item, dict) and str(item.get("value", "")).strip()
                    ],
                    last_seen_channel=str(runtime_data.get("last_seen_channel", "")),
                    last_seen_uid=str(runtime_data.get("last_seen_uid", "")),
                    observed_keywords=set(runtime_data.get("observed_keywords", [])),
                    observed_roles=set(runtime_data.get("observed_roles", [])),
                    observed_emotions=set(runtime_data.get("observed_emotions", [])),
                    observed_entities=set(runtime_data.get("observed_entities", [])),
                    # A1: Deserialize timestamp
                    last_event_processed_at=(
                        datetime.fromisoformat(runtime_data["last_event_processed_at"])
                        if runtime_data.get("last_event_processed_at")
                        else None
                    ),
                ),
            )
            runtime = manager.entries[user_id].runtime
            if not runtime.memory_facts:
                for note in runtime.summary_notes:
                    value = str(note).strip()
                    if not value:
                        continue
                    runtime.memory_facts.append(
                        MemoryFact(
                            fact_type="summary",
                            value=value,
                            source="legacy",
                            confidence=0.4,
                            observed_at="",
                        )
                    )

        manager.speaker_index = dict(payload.get("speaker_index", {}))
        manager.identity_index = dict(payload.get("identity_index", {}))
        if not manager.speaker_index:
            for user_id, entry in manager.entries.items():
                labels = [user_id, entry.profile.name, *entry.profile.aliases]
                for label in labels:
                    if label:
                        manager.speaker_index[manager._normalize_label(label)] = user_id
        if not manager.identity_index:
            for user_id, entry in manager.entries.items():
                for channel, external_id in entry.profile.identities.items():
                    if channel and external_id:
                        manager.identity_index[manager._identity_key(channel, external_id)] = user_id
        return manager
