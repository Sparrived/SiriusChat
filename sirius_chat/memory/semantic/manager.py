"""Semantic memory manager: group norms, atmosphere history, user relationships."""

from __future__ import annotations

import logging
import re
from typing import Any

from sirius_chat.memory.semantic.models import (
    AtmosphereSnapshot,
    GroupSemanticProfile,
    RelationshipState,
    UserSemanticProfile,
)
from sirius_chat.memory.semantic.store import SemanticProfileStore

logger = logging.getLogger(__name__)

_MAX_ATMOSPHERE_HISTORY = 100

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "]+",
    flags=re.UNICODE,
)


class SemanticMemoryManager:
    """Manages semantic profiles with disk persistence.

    - Group norms: inferred from message stream (passive learning)
    - Atmosphere history: recorded after each cognition cycle
    - User relationships: updated per interaction
    - Interest topics: extracted from message content (heuristic keyword counting)
    """

    def __init__(self, work_path: Any) -> None:
        self._store = SemanticProfileStore(work_path)
        self._groups: dict[str, GroupSemanticProfile] = {}
        self._users: dict[str, UserSemanticProfile] = {}
        self._global_users: dict[str, UserSemanticProfile] = {}
        # Pending message contents for LLM-based user profile analysis
        self._pending_user_contents: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Group profiles
    # ------------------------------------------------------------------

    def ensure_group_profile(self, group_id: str) -> GroupSemanticProfile:
        if group_id not in self._groups:
            loaded = self._store.load_group_profile(group_id)
            self._groups[group_id] = loaded or GroupSemanticProfile(group_id=group_id)
        return self._groups[group_id]

    def get_group_profile(self, group_id: str) -> GroupSemanticProfile | None:
        return self.ensure_group_profile(group_id)

    def save_group_profile(self, group_id: str) -> None:
        profile = self._groups.get(group_id)
        if profile is not None:
            self._store.save_group_profile(group_id, profile)

    # ------------------------------------------------------------------
    # User profiles
    # ------------------------------------------------------------------

    def _ensure_global_user(self, user_id: str) -> UserSemanticProfile:
        if user_id not in self._global_users:
            loaded = self._store.load_global_user_profile(user_id)
            self._global_users[user_id] = loaded or UserSemanticProfile(user_id=user_id)
        return self._global_users[user_id]

    def _sync_to_global(self, user_id: str, local: UserSemanticProfile) -> None:
        """Merge group-local user profile into global shared profile.
        Relationship state stays group-local; communication style and
        interest graph are shared cross-group."""
        global_profile = self._ensure_global_user(user_id)
        if local.communication_style and not global_profile.communication_style:
            global_profile.communication_style = local.communication_style
        for item in local.interest_graph:
            if item not in global_profile.interest_graph:
                global_profile.interest_graph.append(item)

    def _seed_from_global(self, group_id: str, user_id: str) -> UserSemanticProfile | None:
        """If a global profile exists, copy cross-group fields into a new
        group-local profile."""
        global_profile = self._global_users.get(user_id)
        if global_profile is None:
            loaded = self._store.load_global_user_profile(user_id)
            if loaded is None:
                return None
            global_profile = loaded
            self._global_users[user_id] = global_profile

        local = UserSemanticProfile(user_id=user_id)
        local.communication_style = global_profile.communication_style
        local.interest_graph = list(global_profile.interest_graph)
        # relationship_state stays default (group-local)
        key = f"{group_id}:{user_id}"
        self._users[key] = local
        return local

    def get_user_profile(self, group_id: str, user_id: str) -> UserSemanticProfile:
        key = f"{group_id}:{user_id}"
        if key not in self._users:
            loaded = self._store.load_user_profile(group_id, user_id)
            if loaded is not None:
                self._users[key] = loaded
            else:
                # Try seed from global profile
                seeded = self._seed_from_global(group_id, user_id)
                if seeded is None:
                    self._users[key] = UserSemanticProfile(user_id=user_id)
        return self._users[key]

    def save_user_profile(self, group_id: str, user_id: str) -> None:
        key = f"{group_id}:{user_id}"
        profile = self._users.get(key)
        if profile is not None:
            self._store.save_user_profile(group_id, user_id, profile)
            self._sync_to_global(user_id, profile)
            self._store.save_global_user_profile(user_id, self._global_users[user_id])

    def enqueue_user_content(self, user_id: str, content: str) -> None:
        """Accumulate user message content for periodic LLM-based profile analysis."""
        if not content or not user_id:
            return
        self._pending_user_contents.setdefault(user_id, []).append(content)

    def get_user_content_batch(self, user_id: str, max_n: int = 10) -> list[str]:
        """Retrieve and clear pending contents for a user."""
        batch = self._pending_user_contents.get(user_id, [])[:max_n]
        if batch:
            remaining = self._pending_user_contents[user_id][max_n:]
            self._pending_user_contents[user_id] = remaining
        return batch

    def set_user_profile_fields(
        self,
        group_id: str,
        user_id: str,
        *,
        name: str = "",
        communication_style: str = "",
        interest_graph: list[Any] | None = None,
    ) -> None:
        """Update name, communication_style and interest_graph for a user."""
        profile = self.get_user_profile(group_id, user_id)
        if name:
            profile.name = name
        if communication_style:
            profile.communication_style = communication_style
        if interest_graph is not None:
            profile.interest_graph = interest_graph
        self.save_user_profile(group_id, user_id)

    def get_global_user_profile(self, user_id: str) -> UserSemanticProfile | None:
        """Get the cross-group shared semantic profile for a user."""
        return self._ensure_global_user(user_id)

    def set_global_user_name(self, user_id: str, name: str) -> None:
        """Set the display name on the global user profile (QQ name)."""
        if not name:
            return
        profile = self._ensure_global_user(user_id)
        if not profile.name:
            profile.name = name
            self._store.save_global_user_profile(user_id, profile)

    def list_group_user_profiles(self, group_id: str) -> list[UserSemanticProfile]:
        return self._store.list_group_user_profiles(group_id)

    # ------------------------------------------------------------------
    # Passive learning: group norms from message stream
    # ------------------------------------------------------------------

    def learn_from_message(
        self,
        group_id: str,
        content: str,
        social_intent: str = "",
    ) -> None:
        """Update group norms from a single message (zero LLM cost)."""
        profile = self.ensure_group_profile(group_id)
        norms = profile.group_norms
        text = content or ""
        length = len(text)

        # 1. Message count and average length
        old_count = norms.get("message_count", 0)
        new_count = old_count + 1
        old_avg = norms.get("avg_message_length", 0.0)
        norms["avg_message_length"] = (old_avg * old_count + length) / new_count
        norms["message_count"] = new_count

        # 2. Length distribution
        bucket = "short" if length < 20 else "medium" if length < 100 else "long"
        dist = norms.get("length_distribution", {})
        dist[bucket] = dist.get(bucket, 0) + 1
        norms["length_distribution"] = dist

        # 3. Emoji usage rate
        has_emoji = bool(_EMOJI_PATTERN.search(text))
        emoji_total = norms.get("emoji_total", 0) + (1 if has_emoji else 0)
        norms["emoji_total"] = emoji_total
        norms["emoji_usage_rate"] = round(emoji_total / new_count, 4)

        # 4. Mention rate
        has_mention = "@" in text
        mention_total = norms.get("mention_total", 0) + (1 if has_mention else 0)
        norms["mention_total"] = mention_total
        norms["mention_rate"] = round(mention_total / new_count, 4)

        # 5. Active hours histogram
        from sirius_chat.core.utils import now_iso
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
        hours = norms.get("active_hours", {})
        hours[str(hour)] = hours.get(str(hour), 0) + 1
        norms["active_hours"] = hours

        # 6. Interaction style inference
        short_ratio = dist.get("short", 0) / new_count
        if short_ratio > 0.6:
            profile.typical_interaction_style = "active"
        elif norms.get("emoji_usage_rate", 0) > 0.3:
            profile.typical_interaction_style = "humorous"
        elif norms.get("mention_rate", 0) > 0.2:
            profile.typical_interaction_style = "formal"
        else:
            profile.typical_interaction_style = "balanced"

        # 7. Topic switch tracking (skip if no prior intent recorded)
        if social_intent:
            last = norms.get("last_intent", "")
            if last and social_intent != last:
                norms["topic_switches"] = norms.get("topic_switches", 0) + 1
            norms["last_intent"] = social_intent
            switches = norms.get("topic_switches", 0)
            norms["topic_switch_frequency"] = round(switches / new_count, 4)

        # 8. Interest topics are now extracted via LLM during diary generation
        # (see DiaryGenerator for dominant_topic / interest_topics extraction).
        # Passive learning only tracks message stats here.

        self.save_group_profile(group_id)

    # ------------------------------------------------------------------
    # Atmosphere recording
    # ------------------------------------------------------------------

    def record_atmosphere(
        self,
        group_id: str,
        valence: float,
        arousal: float,
        active_participants: int = 0,
    ) -> None:
        """Append an atmosphere snapshot to group profile history."""
        from sirius_chat.core.utils import now_iso
        profile = self.ensure_group_profile(group_id)
        profile.atmosphere_history.append(
            AtmosphereSnapshot(
                timestamp=now_iso(),
                group_valence=valence,
                group_arousal=arousal,
                active_participants=active_participants,
            )
        )
        if len(profile.atmosphere_history) > _MAX_ATMOSPHERE_HISTORY:
            profile.atmosphere_history = profile.atmosphere_history[-_MAX_ATMOSPHERE_HISTORY:]
        self.save_group_profile(group_id)

    # ------------------------------------------------------------------
    # Relationship updates
    # ------------------------------------------------------------------

    def update_relationship(
        self,
        group_id: str,
        user_id: str,
        valence: float = 0.0,
        urgency_score: int = 0,
        social_intent: str = "",
        is_mentioned: bool = False,
        burst_detected: bool = False,
    ) -> None:
        """Update relationship state based on interaction signals."""
        from sirius_chat.core.utils import now_iso
        from datetime import datetime, timezone, timedelta

        profile = self.get_user_profile(group_id, user_id)
        rs = profile.relationship_state

        now = datetime.now(timezone.utc)
        now_iso_str = now_iso()
        rs.last_interaction_at = now_iso_str
        if not rs.first_interaction_at:
            rs.first_interaction_at = rs.last_interaction_at

        # Interaction frequency (simplified: +0.05 per interaction, cap at 1.0)
        rs.interaction_frequency_7d = round(min(1.0, rs.interaction_frequency_7d + 0.05), 4)

        # Emotional intimacy: every interaction builds it, strong emotion accelerates
        base_increase = 0.01
        emotion_bonus = abs(valence) * 0.04
        rs.emotional_intimacy = round(
            min(1.0, rs.emotional_intimacy + base_increase + emotion_bonus), 4
        )

        # Trust score: positive feedback
        # 1. High-urgency help requests
        if urgency_score > 70:
            rs.trust_score = round(min(1.0, rs.trust_score + 0.02), 4)
        # 2. Every normal interaction builds trust slowly
        else:
            rs.trust_score = round(min(1.0, rs.trust_score + 0.005), 4)
        # 3. Being mentioned and continuing the conversation shows engagement
        if is_mentioned:
            rs.trust_score = round(min(1.0, rs.trust_score + 0.005), 4)

        # Trust score: negative feedback
        # 1. Burst / spam behavior
        if burst_detected:
            rs.trust_score = round(max(0.1, rs.trust_score - 0.05), 4)

        # Trust score: long-term decay (inactive for >30 days)
        try:
            last_dt = datetime.fromisoformat(rs.last_interaction_at.replace("Z", "+00:00"))
            if now - last_dt > timedelta(days=30):
                rs.trust_score = round(max(0.1, rs.trust_score - 0.02), 4)
        except Exception:
            pass

        # Dependency score from help-seeking intent
        intent_lower = (social_intent or "").lower()
        if "help" in intent_lower or "求助" in intent_lower:
            rs.dependency_score = round(min(1.0, rs.dependency_score + 0.03), 4)

        self.save_user_profile(group_id, user_id)

    def penalize_trust(self, group_id: str, user_id: str, delta: float = 0.03) -> None:
        """Reduce trust score (e.g. after SKILL invocation rejected)."""
        profile = self.get_user_profile(group_id, user_id)
        rs = profile.relationship_state
        rs.trust_score = round(max(0.1, rs.trust_score - delta), 4)
        self.save_user_profile(group_id, user_id)

    # ------------------------------------------------------------------
    # Keyword extraction (heuristic, zero LLM cost)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_keywords(profile: GroupSemanticProfile, text: str) -> None:
        """Simple token frequency heuristic for topic extraction (zero dependencies)."""
        import re

        # Split on punctuation and whitespace, keep alphanumeric tokens >= 2 chars
        punct = r"\s\n\r\t，。！？；：\"\"''（）【】、.,;:!?()\[\]"
        tokens = re.split(f"[{punct}]+", text)
        candidates = [t.strip() for t in tokens if len(t.strip()) >= 2 and t.strip().isalnum()]

        # Update frequency counter in group_norms
        freq = profile.group_norms.get("topic_freq", {})
        for word in candidates:
            freq[word] = freq.get(word, 0) + 1
        profile.group_norms["topic_freq"] = freq

        # Promote high-frequency words to interest_topics
        threshold = max(3, profile.group_norms.get("message_count", 0) // 10)
        for word, count in freq.items():
            if count >= threshold and word not in profile.interest_topics:
                profile.interest_topics.append(word)

        # Update dominant topic
        if freq:
            profile.dominant_topic = max(freq, key=freq.get)
