"""Delayed response queue: hold responses and trigger at natural timing.

Monitors conversation during the wait window:
- If problem solved by others → cancel
- If topic gap appears → trigger immediately
- If topic drifts to AI-relevant → trigger early

v1.0+ 新增：IMMEDIATE 策略也走本队列，但使用极短的防抖窗口（3s），
在同 group 内合并连续 IMMEDIATE 消息，避免刷屏。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sirius_chat.models.response_strategy import (
    DelayedResponseItem,
    ResponseStrategy,
    StrategyDecision,
)

logger = logging.getLogger(__name__)

_GAP_TRIGGER_SECONDS = 10.0
_IMMEDIATE_DEBOUNCE_SECONDS = 8.0


class DelayedResponseQueue:
    """Queue for DELAYED and IMMEDIATE strategy responses."""

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
        multimodal_inputs: list[dict[str, str]] | None = None,
    ) -> DelayedResponseItem:
        """Add an item to the delayed queue.

        For IMMEDIATE strategy, if the same group already has a pending
        IMMEDIATE item, merge the message content and reset the debounce
        timer so rapid-fire messages are consolidated into one reply.
        """
        from sirius_chat.core.utils import now_iso

        # Debounce: merge with any existing pending item in the same group.
        # This prevents multiple independent replies during high-frequency
        # message bursts; all messages within the debounce window are
        # consolidated into one prompt.
        queue = self._queues.get(group_id, [])
        for item in queue:
            if item.status == "pending":
                item.message_content += f"\n{message_content}"
                item.enqueue_time = now_iso()
                # Keep the shorter window so urgent messages are not delayed
                new_window = self._window_for_item(strategy_decision)
                item.window_seconds = min(item.window_seconds, new_window)
                # Upgrade strategy to IMMEDIATE if any merged message is immediate
                if strategy_decision.strategy == ResponseStrategy.IMMEDIATE:
                    item.strategy_decision = strategy_decision
                item.emotion_state.update(emotion_state or {})
                if candidate_memories:
                    item.candidate_memories.extend(candidate_memories)
                if multimodal_inputs:
                    item.multimodal_inputs.extend(multimodal_inputs)
                # Update caller identity to the latest message (most relevant for skill auth)
                item.user_id = user_id
                item.channel = channel
                item.channel_user_id = channel_user_id
                logger.debug(
                    "Merged %s item %s for group %s (content now %d chars, window %.1fs)",
                    strategy_decision.strategy.value,
                    item.item_id,
                    group_id,
                    len(item.message_content),
                    item.window_seconds,
                )
                return item

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
            enqueue_time=now_iso(),
            window_seconds=self._window_for_item(strategy_decision),
            status="pending",
            multimodal_inputs=list(multimodal_inputs or []),
        )
        if group_id not in self._queues:
            self._queues[group_id] = []
        self._queues[group_id].append(item)
        logger.debug(
            "Enqueued %s item %s for group %s",
            strategy_decision.strategy.value,
            item.item_id,
            group_id,
        )
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
        pending = [i for i in queue if i.status == "pending"]
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
        now = datetime.now(timezone.utc)

        # Check if window expired
        enqueue_dt = _parse_iso(item.enqueue_time)
        if enqueue_dt:
            elapsed = (now - enqueue_dt).total_seconds()
            if elapsed >= item.window_seconds:
                logger.debug(
                    "Item %s triggered (window expired: %.1fs >= %.1fs)",
                    item.item_id,
                    elapsed,
                    item.window_seconds,
                )
                return "trigger"
            logger.debug(
                "Item %s waiting (elapsed %.1fs < window %.1fs)",
                item.item_id,
                elapsed,
                item.window_seconds,
            )

        # IMMEDIATE items only check window expiration (no topic gap)
        if item.strategy_decision.strategy == ResponseStrategy.IMMEDIATE:
            return "wait"

        # DELAYED items also check topic gap (trigger)
        if recent_messages:
            last_msg_time = recent_messages[-1].get("timestamp", "")
            last_dt = _parse_iso(last_msg_time)
            if last_dt:
                gap = (now - last_dt).total_seconds()
                if gap >= _GAP_TRIGGER_SECONDS:
                    logger.debug(
                        "Delayed item %s triggered (topic gap: %.1fs >= %.1fs)",
                        item.item_id,
                        gap,
                        _GAP_TRIGGER_SECONDS,
                    )
                    return "trigger"
                logger.debug(
                    "Delayed item %s waiting (topic gap: %.1fs < %.1fs)",
                    item.item_id,
                    gap,
                    _GAP_TRIGGER_SECONDS,
                )

        return "wait"

    @staticmethod
    def _window_for_item(strategy_decision: StrategyDecision) -> float:
        """Return debounce/wait window based on strategy and urgency."""
        if strategy_decision.strategy == ResponseStrategy.IMMEDIATE:
            return _IMMEDIATE_DEBOUNCE_SECONDS
        if strategy_decision.urgency >= 70:
            return 15.0
        if strategy_decision.urgency >= 40:
            return 30.0
        return 60.0


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
