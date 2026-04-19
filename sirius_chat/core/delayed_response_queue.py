"""Delayed response queue: hold responses and trigger at natural timing.

Monitors conversation during the wait window:
- If problem solved by others → cancel
- If topic gap appears → trigger immediately
- If topic drifts to AI-relevant → trigger early
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sirius_chat.models.response_strategy import DelayedResponseItem, StrategyDecision

logger = logging.getLogger(__name__)

_GAP_TRIGGER_SECONDS = 10.0
_CANCEL_KEYWORDS = {"解决了", "好了", "谢谢", "没事了", "ok了", "搞定", "done"}


class DelayedResponseQueue:
    """Queue for DELAYED strategy responses."""

    def __init__(self) -> None:
        # group_id -> list of items
        self._queues: dict[str, list[DelayedResponseItem]] = {}

    def enqueue(
        self,
        group_id: str,
        user_id: str,
        message_content: str,
        strategy_decision: StrategyDecision,
        emotion_state: dict[str, Any] | None = None,
        candidate_memories: list[str] | None = None,
        channel: str | None = None,
        channel_user_id: str | None = None,
    ) -> DelayedResponseItem:
        """Add an item to the delayed queue."""
        item = DelayedResponseItem(
            item_id=f"dri_{uuid.uuid4().hex[:12]}",
            group_id=group_id,
            user_id=user_id,
            channel=channel,
            channel_user_id=channel_user_id,
            message_content=message_content,
            strategy_decision=strategy_decision,
            emotion_state=dict(emotion_state or {}),
            candidate_memories=list(candidate_memories or []),
            enqueue_time=_now_iso(),
            window_seconds=self._window_for_priority(strategy_decision.urgency),
            status="pending",
        )
        if group_id not in self._queues:
            self._queues[group_id] = []
        self._queues[group_id].append(item)
        logger.debug("Enqueued delayed item %s for group %s", item.item_id, group_id)
        return item

    def tick(
        self,
        group_id: str,
        recent_messages: list[dict[str, Any]],
    ) -> list[DelayedResponseItem]:
        """Process queue for a group based on recent conversation.

        Returns items that should be triggered now.
        """
        queue = self._queues.get(group_id, [])
        if not queue:
            return []

        triggered: list[DelayedResponseItem] = []
        remaining: list[DelayedResponseItem] = []

        for item in queue:
            if item.status != "pending":
                continue

            action = self._evaluate_item(item, recent_messages)
            if action == "trigger":
                item.status = "triggered"
                triggered.append(item)
            elif action == "cancel":
                item.status = "cancelled"
                logger.debug("Cancelled delayed item %s (reason: solved)", item.item_id)
            else:
                remaining.append(item)

        self._queues[group_id] = remaining
        return triggered

    def cancel_all_for_user(self, group_id: str, user_id: str) -> int:
        """Cancel all pending items for a user in a group."""
        queue = self._queues.get(group_id, [])
        cancelled = 0
        for item in queue:
            if item.user_id == user_id and item.status == "pending":
                item.status = "cancelled"
                cancelled += 1
        return cancelled

    def get_pending(self, group_id: str) -> list[DelayedResponseItem]:
        """Get all pending items for a group."""
        return [i for i in self._queues.get(group_id, []) if i.status == "pending"]

    def clear_group(self, group_id: str) -> None:
        """Clear all items for a group."""
        self._queues.pop(group_id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evaluate_item(
        self,
        item: DelayedResponseItem,
        recent_messages: list[dict[str, Any]],
    ) -> str:
        """Evaluate whether to trigger, cancel, or keep waiting."""
        # Check if window expired
        enqueue_dt = _parse_iso(item.enqueue_time)
        if enqueue_dt:
            elapsed = (datetime.now(timezone.utc) - enqueue_dt).total_seconds()
            if elapsed >= item.window_seconds:
                return "trigger"

        # Check if problem solved (cancel)
        for msg in recent_messages:
            content = str(msg.get("content", ""))
            if any(kw in content for kw in _CANCEL_KEYWORDS):
                return "cancel"

        # Check for topic gap (trigger)
        if recent_messages:
            last_msg_time = recent_messages[-1].get("timestamp", "")
            last_dt = _parse_iso(last_msg_time)
            if last_dt:
                gap = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if gap >= _GAP_TRIGGER_SECONDS:
                    return "trigger"

        return "wait"

    @staticmethod
    def _window_for_priority(urgency: float) -> float:
        if urgency >= 70:
            return 15.0
        if urgency >= 40:
            return 30.0
        return 60.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
