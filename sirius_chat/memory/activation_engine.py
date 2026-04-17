"""Activation engine: Ebbinghaus forgetting curve + access reinforcement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class DecaySchedule:
    """Differentiated decay parameters by memory type (paper §4.2.4)."""

    core_preference_lambda: float = 0.001    # Almost permanent (name, residence)
    transient_state_lambda: float = 0.05     # Fades in weeks (mood, temporary)
    timely_info_lambda: float = 0.1          # Fades after event (deadlines, news)
    default_lambda: float = 0.01             # General fallback
    reinforcement_gamma: float = 0.1         # Access boost coefficient
    archive_threshold: float = 0.1           # Activation below this → hibernate


class ActivationEngine:
    """Calculates and updates memory activation scores."""

    def __init__(self, schedule: DecaySchedule | None = None) -> None:
        self.schedule = schedule or DecaySchedule()

    def _resolve_lambda(self, memory_category: str) -> float:
        """Select decay lambda based on memory category."""
        category = (memory_category or "custom").lower()
        if category in ("identity", "preference"):
            return self.schedule.core_preference_lambda
        if category in ("emotion", "transient"):
            return self.schedule.transient_state_lambda
        if category in ("event", "timely"):
            return self.schedule.timely_info_lambda
        return self.schedule.default_lambda

    def calculate_activation(
        self,
        importance: float,
        created_at: str,
        access_count: int,
        memory_category: str = "custom",
    ) -> float:
        """Calculate current activation score.

        Formula (paper §4.2.4):
            activation = importance_baseline × time_decay × access_boost
            time_decay = exp(-λ × hours_since_creation)
            access_boost = 1 + γ × access_count
        """
        hours = self._hours_since(created_at)
        if hours is None:
            return importance

        decay_lambda = self._resolve_lambda(memory_category)
        time_decay = math.exp(-decay_lambda * hours)
        access_boost = 1.0 + self.schedule.reinforcement_gamma * access_count
        activation = importance * time_decay * access_boost
        return max(0.0, min(1.0, activation))

    def should_archive(self, activation: float) -> bool:
        """Check if a memory should be moved to archive (hibernation)."""
        return activation < self.schedule.archive_threshold

    def on_access(
        self,
        importance: float,
        created_at: str,
        access_count: int,
        memory_category: str = "custom",
    ) -> tuple[float, int]:
        """Update activation when a memory is retrieved/used.

        Returns:
            (new_activation, new_access_count)
        """
        new_count = access_count + 1
        new_activation = self.calculate_activation(
            importance=importance,
            created_at=created_at,
            access_count=new_count,
            memory_category=memory_category,
        )
        return new_activation, new_count

    def recalculate_all(
        self,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Recalculate activation for a batch of memory items.

        Each item dict must contain: importance, created_at, access_count,
        and optionally memory_category.
        """
        results = []
        for item in items:
            activation = self.calculate_activation(
                importance=float(item.get("importance", 0.5)),
                created_at=str(item.get("created_at", "")),
                access_count=int(item.get("access_count", 0)),
                memory_category=str(item.get("memory_category", "custom")),
            )
            new_item = dict(item)
            new_item["activation"] = round(activation, 6)
            results.append(new_item)
        return results

    @staticmethod
    def _hours_since(iso_timestamp: str) -> float | None:
        """Compute hours elapsed since ISO timestamp."""
        if not iso_timestamp:
            return None
        try:
            dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - dt
            return delta.total_seconds() / 3600.0
        except (ValueError, TypeError):
            return None
