"""User memory manager implementation"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sirius_chat.memory.user.models import MemoryFact, UserMemoryEntry, UserProfile, UserRuntimeState
from sirius_chat.trait_taxonomy import TRAIT_TAXONOMY

logger = logging.getLogger(__name__)

# ============================================================================
# Performance optimization constants (defaults, overridden by MemoryPolicy)
# ============================================================================

# C1: Memory Facts upper limit management
MAX_MEMORY_FACTS = 50  # Maximum memory facts per user

# A1: Time window deduplication (minutes)
EVENT_DEDUP_WINDOW_MINUTES = 5

# Observed sets upper limit
MAX_OBSERVED_SET_SIZE = 100


class UserMemoryManager:
    """Manages user memory entries, facts, and profiles."""
    
    def __init__(self):
        # v0.28: group-isolated entries: {group_id: {user_id: UserMemoryEntry}}
        self.entries: dict[str, dict[str, UserMemoryEntry]] = {}
        # Legacy flat index (backward compat): {user_id: UserMemoryEntry}
        # Populated lazily from default group when old callers access it.
        self.speaker_index: dict[str, str] = {}
        self.identity_index: dict[str, str] = {}

    def _ensure_group(self, group_id: str) -> dict[str, UserMemoryEntry]:
        """Get or create the user entry dict for a group."""
        if group_id not in self.entries:
            self.entries[group_id] = {}
        return self.entries[group_id]

    @staticmethod
    def _normalize_label(label: str) -> str:
        return label.strip().lower()

    @staticmethod
    def _identity_key(channel: str, external_user_id: str) -> str:
        return f"{channel.strip().lower()}:{external_user_id.strip().lower()}"

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

    def _append_inferred_alias(self, *, entry: UserMemoryEntry, alias: str, max_aliases: int = 5) -> bool:
        value = alias.strip()
        if not value:
            return False
        normalized = self._normalize_label(value)
        trusted_labels = {
            self._normalize_label(label)
            for label in [entry.profile.name, entry.profile.user_id, *entry.profile.aliases]
            if label.strip()
        }
        if normalized in trusted_labels:
            return False
        if any(self._normalize_label(existing) == normalized for existing in entry.runtime.inferred_aliases):
            return False
        entry.runtime.inferred_aliases.append(value)
        if len(entry.runtime.inferred_aliases) > max_aliases:
            entry.runtime.inferred_aliases = entry.runtime.inferred_aliases[-max_aliases:]
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
        memory_category: str = "custom",
        context_channel: str = "",
        context_topic: str = "",
        source_event_id: str = "",
        group_id: str = "default",
    ) -> None:
        """Add memory fact with trait normalization and intelligent upper limit management.
        
        C1 approach: When exceeding max_facts, delete lowest-confidence facts rather than simple FIFO.
        B approach: Auto-apply trait normalization for certain fact_types.
        """
        group = self._ensure_group(group_id)
        entry = group.get(user_id)
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
        
        from sirius_chat.core.utils import now_iso
        timestamp = observed_at or now_iso()
        normalized = self._normalize_summary_note(text)
        
        # Check if similar fact exists, update confidence and increment mention_count
        for item in entry.runtime.memory_facts:
            if item.fact_type != fact_type:
                continue
            if self._normalize_summary_note(item.value) != normalized:
                continue
            item.mention_count += 1
            if confidence > item.confidence:
                item.confidence = max(0.0, min(1.0, confidence))
                item.source = source
                item.observed_at = timestamp
            return
        
        # Add new fact
        entry.runtime.memory_facts.append(
            MemoryFact(
                fact_type=fact_type,
                value=text,
                source=source,
                confidence=confidence,
                observed_at=timestamp,
                memory_category=memory_category,
                context_channel=context_channel,
                context_topic=context_topic,
                source_event_id=source_event_id,
                mention_count=1,
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

    def register_user(self, profile: UserProfile, group_id: str = "default") -> None:
        """Register a user profile in a group."""
        if not profile.user_id:
            profile.user_id = profile.name
        group = self._ensure_group(group_id)
        if profile.user_id not in group:
            group[profile.user_id] = UserMemoryEntry(profile=profile)
        else:
            existing = group[profile.user_id].profile
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
        # Search across all groups for this user
        for group in self.entries.values():
            entry = group.get(user_id)
            if entry is not None:
                return entry
        return None

    def ensure_user(self, *, speaker: str, persona: str = "", group_id: str = "default") -> UserProfile:
        """Ensure user exists, creating if necessary."""
        resolved_user_id = self.resolve_user_id(speaker=speaker)
        group = self._ensure_group(group_id)
        if resolved_user_id and resolved_user_id in group:
            entry = group[resolved_user_id]
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
        group_id: str = "default",
    ) -> None:
        """Remember a message from user."""
        group = self._ensure_group(group_id)
        is_new_user = profile.user_id not in group
        self.register_user(profile, group_id=group_id)
        entry = group[profile.user_id]
        entry.runtime.recent_messages.append(content)
        if len(entry.runtime.recent_messages) > max_recent_messages:
            entry.runtime.recent_messages = entry.runtime.recent_messages[-max_recent_messages:]
        if channel:
            entry.runtime.last_seen_channel = channel
        if channel_user_id:
            entry.runtime.last_seen_uid = channel_user_id
        
        # Heuristic: When first seeing a user, add initial summary note from their message
        # This ensures summary_notes and memory_facts are populated even if memory_extract task is disabled
        if is_new_user and content.strip():
            clean_content = content.strip()[:100]  # Truncate if too long
            appended = self._append_summary_note(entry=entry, note=clean_content, max_notes=8)
            # Also create memory fact to match the summary note
            if appended:
                self.add_memory_fact(
                    user_id=profile.user_id,
                    fact_type="summary",
                    value=clean_content,
                    source="heuristic",
                    confidence=0.3,  # Low confidence for heuristic extraction
                )

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
        group_id: str = "default",
    ) -> None:
        """Apply AI inferred runtime updates to user memory."""
        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return
        if inferred_persona:
            entry.runtime.inferred_persona = inferred_persona
        if inferred_aliases:
            for alias in inferred_aliases:
                self._append_inferred_alias(entry=entry, alias=alias)
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

    def add_summary_note(self, *, user_id: str, note: str, max_notes: int = 8, group_id: str = "default") -> None:
        """Add a summary note for user."""
        entry = self._ensure_group(group_id).get(user_id)
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
        for group_id, group_entries in other.entries.items():
            for user_id, incoming in group_entries.items():
                incoming_profile = UserProfile(
                    user_id=incoming.profile.user_id,
                    name=incoming.profile.name,
                    persona=incoming.profile.persona,
                    identities=dict(incoming.profile.identities),
                    aliases=list(incoming.profile.aliases),
                    traits=list(incoming.profile.traits),
                    metadata=dict(incoming.profile.metadata),
                )
                self.register_user(incoming_profile, group_id=group_id)
                current = self._ensure_group(group_id)[user_id]

                if incoming.runtime.inferred_persona and not current.runtime.inferred_persona:
                    current.runtime.inferred_persona = incoming.runtime.inferred_persona

                for trait in incoming.runtime.inferred_traits:
                    if trait not in current.runtime.inferred_traits:
                        current.runtime.inferred_traits.append(trait)

                for alias in incoming.runtime.inferred_aliases:
                    self._append_inferred_alias(entry=current, alias=alias)

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
                        group_id=group_id,
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
    
    def cleanup_expired_memories(self, min_quality: float = 0.25, group_id: str | None = None) -> dict[str, int]:
        """Clean up expired/low-quality memories for all users.
        
        Returns: {user_id: number of deleted memories}
        """
        from sirius_chat.memory.quality.models import MemoryForgetEngine
        cleanup_stats = {}
        groups_to_clean = [group_id] if group_id else list(self.entries.keys())
        for gid in groups_to_clean:
            group = self.entries.get(gid, {})
            for user_id, entry in group.items():
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
        source_event_id: str = "",
        group_id: str = "default",
    ) -> None:
        """Convert event features to user memory facts and feature signals.
        
        Auto-converts event's emotion_tags, keywords, role_slots, time_hints etc.
        to corresponding user memory facts and updates observed feature sets.
        """
        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return

        max_set = MAX_OBSERVED_SET_SIZE

        # 1. Emotion recognition → user feature signals and memory facts
        emotions = event_features.get("emotion_tags", [])
        if isinstance(emotions, list) and emotions:
            clean_emotions = [str(e).strip() for e in emotions if str(e).strip()]
            entry.runtime.observed_emotions.update(clean_emotions)
            self._cap_set(entry.runtime.observed_emotions, max_set)
            
            emotion_str = ", ".join(clean_emotions[:3])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="emotional_pattern",
                value=f"Expressed emotions: {emotion_str}",
                source=source,
                confidence=base_confidence - 0.05,
                memory_category="emotion",
                source_event_id=source_event_id,
            )

        # 2. Keyword accumulation → user interests and memory facts
        keywords = event_features.get("keywords", [])
        if isinstance(keywords, list) and keywords:
            clean_keywords = [str(k).strip() for k in keywords if str(k).strip()]
            entry.runtime.observed_keywords.update(clean_keywords)
            self._cap_set(entry.runtime.observed_keywords, max_set)
            
            # Take top 5 keywords to avoid length
            keywords_str = ", ".join(clean_keywords[:5])
            self.add_memory_fact(
                user_id=user_id,
                fact_type="user_interest",
                value=f"Topics of interest: {keywords_str}",
                source=source,
                confidence=base_confidence - 0.1,
                memory_category="preference",
                source_event_id=source_event_id,
            )

        # 3. Role recognition → social network and feature lifting
        roles = event_features.get("role_slots", [])
        if isinstance(roles, list) and roles:
            clean_roles = [str(r).strip() for r in roles if str(r).strip()]
            entry.runtime.observed_roles.update(clean_roles)
            self._cap_set(entry.runtime.observed_roles, max_set)
            
            roles_str = ", ".join(set(clean_roles))
            self.add_memory_fact(
                user_id=user_id,
                fact_type="social_context",
                value=f"Interacts with roles: {roles_str}",
                source=source,
                confidence=base_confidence - 0.05,
                memory_category="event",
                source_event_id=source_event_id,
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
            self._cap_set(entry.runtime.observed_entities, max_set)

    @staticmethod
    def _cap_set(s: set[str], max_size: int) -> None:
        """Cap a set to max_size by removing arbitrary excess elements."""
        while len(s) > max_size:
            s.pop()

    def get_resident_facts(self, user_id: str, threshold: float = 0.85, group_id: str = "default") -> list[MemoryFact]:
        """Get high-confidence RESIDENT facts (only for persistence to user.json).
        
        RESIDENT: confidence > threshold, representing core, stable user traits and preferences.
        These facts should be persisted to storage.
        """
        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence > threshold
        ]

    def get_transient_facts(self, user_id: str, threshold: float = 0.85, group_id: str = "default") -> list[MemoryFact]:
        """Get low-confidence TRANSIENT facts (stored in session memory).
        
        TRANSIENT: confidence <= threshold, representing recently observed uncertain information.
        These facts should be stored in session memory and auto-cleaned after 30 minutes.
        """
        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return []
        
        return [
            fact for fact in entry.runtime.memory_facts
            if fact.confidence <= threshold
        ]

    def get_user_by_id(self, user_id: str, group_id: str = "default") -> UserMemoryEntry | None:
        """Get user memory entry by exact user ID.
        
        Args:
            user_id: The user ID to look up
            group_id: Group context (default: "default")
        
        Returns:
            UserMemoryEntry or None if not found
        """
        return self._ensure_group(group_id).get(user_id)

    def search_users_by_fact(
        self, 
        fact_type: str, 
        value: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, list[MemoryFact]]:
        """Search for users with specific fact types or values.
        
        Args:
            fact_type: The type of fact to search for (e.g., "job", "location", "hobby")
            value: Optional specific value to match. If None, returns all facts of that type.
            group_id: If provided, search only this group; otherwise search all groups.
        
        Returns:
            Dict mapping user_id to list of matching MemoryFact objects
        """
        results: dict[str, list[MemoryFact]] = {}
        
        groups_to_search = [group_id] if group_id else list(self.entries.keys())
        for gid in groups_to_search:
            group = self.entries.get(gid, {})
            for user_id, entry in group.items():
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
        max_facts_per_type: int = 5,
        group_id: str = "default",
    ) -> dict[str, Any]:
        """Generate a model-friendly user summary with rich context.
        
        This summary is suitable for injection into system prompts or as context
        for the AI model to provide personalized responses.
        
        Args:
            user_id: The user ID to generate summary for
            include_transient: Whether to include low-confidence transient facts
            max_facts_per_type: Maximum number of facts per type in the summary
        
        Returns:
            Dict with keys: profile, summary, traits, interests, recent_facts, 
                           identities, confidence_distribution, channels
        """
        entry = self.get_user_by_id(user_id, group_id=group_id)
        if entry is None:
            return {}
        
        profile = entry.profile
        runtime = entry.runtime
        
        # Separate facts by confidence
        resident_facts = self.get_resident_facts(user_id, group_id=group_id)
        transient_facts = self.get_transient_facts(user_id, group_id=group_id)
        
        # Build facts list based on include_transient flag
        facts_to_include = resident_facts
        if include_transient:
            facts_to_include.extend(transient_facts)
        
        # Group facts by type for organized summary (sorted by confidence desc, capped)
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
                "observed_at": getattr(fact, "observed_at", ""),
                "channel": getattr(fact, "context_channel", ""),
                "topic": getattr(fact, "context_topic", ""),
            }
            # Clean up empty fields
            fact_info = {k: v for k, v in fact_info.items() if v}
            facts_by_type[fact_type].append(fact_info)
        
        # Sort each type by confidence and cap at max_facts_per_type
        for fact_type in facts_by_type:
            facts_by_type[fact_type] = sorted(
                facts_by_type[fact_type],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[:max_facts_per_type]
        
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
            "weak_aliases": runtime.inferred_aliases[:3],
            "persona": profile.persona,
            "inferred_persona": runtime.inferred_persona,
            "traits": key_traits,
            "interests": key_interests,
            "recent_summary": runtime.summary_notes[:3] if runtime.summary_notes else [],
            "facts_by_type": facts_by_type,
            "identities": {channel: uid for channel, uid in profile.identities.items()},
            "channels": list(observed_channels),
            "observed_entities": list(runtime.observed_entities)[:10],  # Top 10 entities
            "last_fact_at": max(
                (f.observed_at for f in facts_to_include if getattr(f, "observed_at", "")),
                default="",
            ),
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
        group_id: str = "default",
    ) -> list[MemoryFact]:
        """Get facts filtered by communication channel and/or topic.
        
        Args:
            user_id: The user ID to query
            channel: Optional channel filter (e.g., "qq", "wechat", "email")
            topic: Optional topic filter (e.g., "work", "hobby", "family")
        
        Returns:
            List of MemoryFact objects matching the filters
        """
        entry = self.get_user_by_id(user_id, group_id=group_id)
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
        transient_threshold: float = 0.85,
        group_id: str = "default",
    ) -> int:
        """Clean up expired TRANSIENT facts.
        
        TRANSIENT facts (confidence <= threshold) are deleted after max_age_minutes
        from their observed_at time. Returns number of deleted facts.
        """
        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return 0
        
        now = datetime.now(timezone.utc)
        deleted_count = 0
        facts_to_keep = []
        
        for fact in entry.runtime.memory_facts:
            # Only check transient facts (dynamic derivation)
            if not fact.is_transient(transient_threshold):
                facts_to_keep.append(fact)
                continue
            
            # Check expiry based on observed_at
            if fact.observed_at:
                try:
                    observed_time = datetime.fromisoformat(fact.observed_at)
                    age_minutes = (now - observed_time).total_seconds() / 60
                    if age_minutes > max_age_minutes:
                        deleted_count += 1
                        continue  # Delete this fact
                except (ValueError, TypeError):
                    pass
            
            facts_to_keep.append(fact)
        
        entry.runtime.memory_facts = facts_to_keep
        
        if deleted_count > 0:
            logger.debug(
                f"Cleaned up {deleted_count} expired transient facts for user {user_id}"
            )
        
        return deleted_count

    async def consolidate_summary_notes(
        self,
        user_id: str,
        provider_async: Any,
        model_name: str,
        min_notes: int = 4,
        temperature: float = 0.3,
        max_tokens: int = 256,
        group_id: str = "default",
    ) -> int:
        """Consolidate summary notes for a user into fewer, more refined notes.

        Uses LLM to merge and summarize multiple notes into concise summaries.
        Returns the number of notes removed (net reduction).
        """
        from sirius_chat.providers.base import GenerationRequest

        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return 0

        notes = entry.runtime.summary_notes
        if len(notes) < min_notes:
            return 0

        system_prompt = (
            "你是摘要归纳器。请将以下用户摘要合并为更少、更精炼的条目。\n"
            "规则：\n- 保留关键信息，去除重复\n- 每条不超过50字\n- 合并含义相似的条目\n"
            "严格输出 JSON 数组，每个元素为 string（归纳后的摘要）。"
        )
        user_prompt = f"摘要列表:\n{json.dumps(notes, ensure_ascii=False, indent=2)}"

        request = GenerationRequest(
            model=model_name,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="summary_consolidation",
        )

        try:
            raw = await provider_async.generate_async(request)
        except Exception as exc:
            logger.warning("摘要归纳 LLM 调用失败 (user=%s): %s", user_id, exc)
            return 0

        parsed = self._parse_string_array(raw)
        if not parsed:
            return 0

        old_count = len(notes)
        entry.runtime.summary_notes = [s[:100] for s in parsed if s.strip()][:8]
        removed = old_count - len(entry.runtime.summary_notes)
        if removed > 0:
            logger.info("给 %s 的笔记整理好了，从 %d 条浓缩到 %d 条，去掉了很多重复的。", user_id, old_count, len(entry.runtime.summary_notes))
        return removed

    async def consolidate_memory_facts(
        self,
        user_id: str,
        provider_async: Any,
        model_name: str,
        min_facts: int = 15,
        temperature: float = 0.3,
        max_tokens: int = 512,
        group_id: str = "default",
    ) -> int:
        """Consolidate memory facts for a user using LLM-based merging.

        Group facts by type, merge similar ones, and produce refined facts.
        Returns the number of facts removed (net reduction).
        """
        from sirius_chat.providers.base import GenerationRequest

        entry = self._ensure_group(group_id).get(user_id)
        if entry is None:
            return 0

        facts = entry.runtime.memory_facts
        if len(facts) < min_facts:
            return 0

        facts_json = [
            {"fact_type": f.fact_type, "value": f.value, "confidence": f.confidence,
             "category": f.memory_category, "mention_count": f.mention_count}
            for f in facts
        ]

        system_prompt = (
            "你是记忆事实归纳器。将以下用户记忆事实合并为更少、更精炼的条目。\n"
            "规则：\n- 合并含义相似或重复的事实\n- 保留关键信息\n- 每条 value 不超过50字\n"
            "- confidence 取合并条目中的最高值\n- mention_count 取合并条目的总和\n"
            "严格输出 JSON 数组，每个元素包含：fact_type, value, confidence, category, mention_count"
        )
        user_prompt = f"记忆事实:\n{json.dumps(facts_json, ensure_ascii=False, indent=2)}"

        request = GenerationRequest(
            model=model_name,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="fact_consolidation",
        )

        try:
            raw = await provider_async.generate_async(request)
        except Exception as exc:
            logger.warning("事实归纳 LLM 调用失败 (user=%s): %s", user_id, exc)
            return 0

        parsed = self._parse_dict_array(raw)
        if not parsed:
            return 0

        old_count = len(facts)
        from sirius_chat.core.utils import now_iso
        _now = now_iso()
        new_facts = []
        for item in parsed:
            value = str(item.get("value", "")).strip()[:100]
            if not value:
                continue
            new_facts.append(MemoryFact(
                fact_type=str(item.get("fact_type", "summary")).strip() or "summary",
                value=value,
                source="consolidation",
                confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                observed_at=_now,
                memory_category=str(item.get("category", "custom")).strip() or "custom",
                validated=True,
                mention_count=max(1, int(item.get("mention_count", 1))),
            ))

        if new_facts:
            entry.runtime.memory_facts = new_facts
            removed = old_count - len(new_facts)
            if removed > 0:
                logger.info("帮 %s 整理记忆档案啦，%d 条压缩成 %d 条，清爽多了。", user_id, old_count, len(new_facts))
            return removed
        return 0

    @staticmethod
    def _parse_string_array(raw: str) -> list[str]:
        """Parse LLM response as JSON string array."""
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
            return []
        if isinstance(result, list):
            return [str(item).strip() for item in result if isinstance(item, str) and item.strip()]
        return []

    @staticmethod
    def _parse_dict_array(raw: str) -> list[dict[str, Any]]:
        """Parse LLM response as JSON dict array."""
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
            return []
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def compress_memory_facts(
        self,
        user_id: str,
        similarity_threshold: float = 0.8,
        group_id: str = "default",
    ) -> int:
        """C3 approach: Dynamic memory facts compression.
        
        Cluster and merge same-type facts to reduce redundant information.
        
        Args:
            user_id: User ID to compress
            similarity_threshold: Similarity threshold (0.0-1.0)
        
        Returns:
            Number of compressed/deleted facts
        """
        entry = self._ensure_group(group_id).get(user_id)
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
                f"帮 {user_id} 压缩了一下记忆档案："
                f"{original_count} 条变成 {len(compressed_facts)} 条，删掉了 {deleted_count} 条冗余的。"
            )
        
        return deleted_count

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary with group isolation preserved."""
        return {
            "entries": {
                group_id: {
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
                            "inferred_aliases": entry.runtime.inferred_aliases,
                            "inferred_traits": entry.runtime.inferred_traits,
                            "preference_tags": entry.runtime.preference_tags,
                            "recent_messages": entry.runtime.recent_messages,
                            "summary_notes": entry.runtime.summary_notes,
                            # Use MemoryFact.to_dict() so any future fields are included automatically
                            "memory_facts": [item.to_dict() for item in entry.runtime.memory_facts],
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
                    for user_id, entry in group_entries.items()
                }
                for group_id, group_entries in self.entries.items()
            },
            "speaker_index": self.speaker_index,
            "identity_index": self.identity_index,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserMemoryManager":
        """Deserialize from group-isolated dictionary."""
        manager = cls()
        raw_entries = payload.get("entries", {})
        for group_id, group_entries in raw_entries.items():
            if not isinstance(group_entries, dict):
                continue
            target_group = manager._ensure_group(group_id)
            for user_id, item in group_entries.items():
                _deserialize_entry(manager, user_id, item, target_group)
        manager.speaker_index = dict(payload.get("speaker_index", {}))
        manager.identity_index = dict(payload.get("identity_index", {}))
        if not manager.speaker_index:
            for group in manager.entries.values():
                for user_id, entry in group.items():
                    labels = [user_id, entry.profile.name, *entry.profile.aliases]
                    for label in labels:
                        if label:
                            manager.speaker_index[manager._normalize_label(label)] = user_id
        if not manager.identity_index:
            for group in manager.entries.values():
                for user_id, entry in group.items():
                    for channel, external_id in entry.profile.identities.items():
                        if channel and external_id:
                            manager.identity_index[manager._identity_key(channel, external_id)] = user_id
        return manager


def _deserialize_entry(
    manager: UserMemoryManager,
    user_id: str,
    item: dict[str, Any],
    target_group: dict[str, UserMemoryEntry],
) -> None:
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
    target_group[user_id] = UserMemoryEntry(
        profile=profile,
        runtime=UserRuntimeState(
            inferred_persona=str(runtime_data.get("inferred_persona", "")),
            inferred_aliases=list(runtime_data.get("inferred_aliases", [])),
            inferred_traits=list(runtime_data.get("inferred_traits", [])),
            preference_tags=list(runtime_data.get("preference_tags", [])),
            recent_messages=list(runtime_data.get("recent_messages", [])),
            summary_notes=list(runtime_data.get("summary_notes", [])),
            memory_facts=[
                MemoryFact.from_dict(item)
                for item in list(runtime_data.get("memory_facts", []))
                if isinstance(item, dict) and str(item.get("value", "")).strip()
            ],
            last_seen_channel=str(runtime_data.get("last_seen_channel", "")),
            last_seen_uid=str(runtime_data.get("last_seen_uid", "")),
            observed_keywords=set(runtime_data.get("observed_keywords", [])),
            observed_roles=set(runtime_data.get("observed_roles", [])),
            observed_emotions=set(runtime_data.get("observed_emotions", [])),
            observed_entities=set(runtime_data.get("observed_entities", [])),
            last_event_processed_at=(
                datetime.fromisoformat(str(runtime_data["last_event_processed_at"]))
                if isinstance(runtime_data.get("last_event_processed_at"), str)
                and str(runtime_data.get("last_event_processed_at", "")).strip()
                else None
            ),
        ),
    )
