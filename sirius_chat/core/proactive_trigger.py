"""Proactive trigger: initiate conversation without direct stimulus (paper §2.3.4).

Trigger types:
- Time: group silent for too long
- Memory: important date, topic update, user return after absence
- Emotion: group atmosphere low, emotional island detected
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_SILENCE_THRESHOLD_MINUTES = 30
_MAX_PER_USER_PER_DAY = 2
_MAX_PER_GROUP_PER_HOUR = 1


class ProactiveTrigger:
    """Decides when to proactively initiate conversation."""

    def __init__(
        self,
        silence_threshold_minutes: float = _DEFAULT_SILENCE_THRESHOLD_MINUTES,
        max_per_user_per_day: int = _MAX_PER_USER_PER_DAY,
        max_per_group_per_hour: int = _MAX_PER_GROUP_PER_HOUR,
    ) -> None:
        self.silence_threshold = timedelta(minutes=silence_threshold_minutes)
        self.max_per_user_per_day = max_per_user_per_day
        self.max_per_group_per_hour = max_per_group_per_hour

        # Tracking counters
        self._user_counts: dict[str, list[str]] = {}  # user_id -> list of ISO dates
        self._group_counts: dict[str, list[str]] = {}  # group_id -> list of ISO hours
        self._last_proactive: dict[str, str] = {}  # group_id -> timestamp

    def check(
        self,
        group_id: str,
        *,
        last_message_at: str | None = None,
        group_atmosphere: dict[str, Any] | None = None,
        important_dates: list[dict[str, str]] | None = None,
    ) -> dict[str, Any] | None:
        """Check if proactive trigger should fire.

        Returns trigger context dict if should fire, None otherwise.
        """
        now = datetime.now(timezone.utc)

        # Cooldown check
        last = self._last_proactive.get(group_id)
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if now - last_dt < timedelta(minutes=10):
                return None

        # Rate limit check
        if self._group_rate_limited(group_id):
            return None

        # 1. Silence trigger
        if last_message_at:
            last_msg_dt = datetime.fromisoformat(last_message_at.replace("Z", "+00:00"))
            if now - last_msg_dt >= self.silence_threshold:
                self._record(group_id, None)
                return {
                    "trigger_type": "silence",
                    "group_id": group_id,
                    "silence_minutes": (now - last_msg_dt).total_seconds() / 60,
                    "suggested_tone": "casual",
                }

        # 2. Atmosphere trigger
        if group_atmosphere:
            valence = group_atmosphere.get("valence", 0.0)
            if valence < -0.3:
                self._record(group_id, None)
                return {
                    "trigger_type": "atmosphere",
                    "group_id": group_id,
                    "valence": valence,
                    "suggested_tone": "empathetic",
                }

        # 3. Memory trigger (important dates)
        if important_dates:
            today = now.strftime("%Y-%m-%d")
            for date_item in important_dates:
                if date_item.get("date", "") == today:
                    user_id = date_item.get("user_id", "")
                    if not self._user_rate_limited(user_id):
                        self._record(group_id, user_id)
                        return {
                            "trigger_type": "memory",
                            "group_id": group_id,
                            "user_id": user_id,
                            "event": date_item.get("event", ""),
                            "suggested_tone": "warm",
                        }

        return None

    def _record(self, group_id: str, user_id: str | None) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self._last_proactive[group_id] = now_iso

        hour_key = now_iso[:13]  # "2026-04-17T20"
        self._group_counts.setdefault(group_id, []).append(hour_key)

        if user_id:
            day_key = now_iso[:10]  # "2026-04-17"
            self._user_counts.setdefault(user_id, []).append(day_key)

    def _group_rate_limited(self, group_id: str) -> bool:
        hour_key = datetime.now(timezone.utc).isoformat()[:13]
        counts = [h for h in self._group_counts.get(group_id, []) if h == hour_key]
        return len(counts) >= self.max_per_group_per_hour

    def _user_rate_limited(self, user_id: str) -> bool:
        day_key = datetime.now(timezone.utc).isoformat()[:10]
        counts = [d for d in self._user_counts.get(user_id, []) if d == day_key]
        return len(counts) >= self.max_per_user_per_day
