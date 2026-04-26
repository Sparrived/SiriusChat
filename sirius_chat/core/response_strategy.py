"""Response strategy engine: four-layer decision system (paper §2.3 / §6).

    IMMEDIATE → DELAYED → SILENT → PROACTIVE
"""

from __future__ import annotations

import logging
from typing import Any

from sirius_chat.models.intent_v3 import IntentAnalysisV3, SocialIntent
from sirius_chat.models.response_strategy import ResponseStrategy, StrategyDecision

logger = logging.getLogger(__name__)


class ResponseStrategyEngine:
    """Decides response strategy based on intent, emotion, and context."""

    def decide(
        self,
        intent: IntentAnalysisV3,
        *,
        is_mentioned: bool = False,
        is_developer: bool = False,
        heat_level: str = "warm",
    ) -> StrategyDecision:
        """Decide response strategy from intent analysis.

        Decision matrix:
            urgency >= 80 and relevance >= 0.7  → IMMEDIATE
            urgency >= 50 and relevance >= 0.55 → DELAYED (high priority)
            urgency >= 30 and relevance >= 0.5  → DELAYED (low priority)
            else                                → SILENT

        Heat suppression:
            hot:       urgency × 0.85, relevance × 0.92
            overheated: urgency × 0.68, relevance × 0.85
        """
        urgency = intent.urgency_score
        relevance = intent.relevance_score
        threshold = intent.threshold

        # Heat suppression: reduce scores in hot/overheated groups
        heat_mult = {"cold": 1.0, "warm": 1.0, "hot": 0.85, "overheated": 0.68}
        rel_mult = {"cold": 1.0, "warm": 1.0, "hot": 0.92, "overheated": 0.85}
        urgency *= heat_mult.get(heat_level, 1.0)
        relevance *= rel_mult.get(heat_level, 1.0)

        # Special rules
        if is_mentioned and intent.social_intent == SocialIntent.HELP_SEEKING:
            # In overheated groups, even direct help-seeking mentions go delayed
            if heat_level == "overheated":
                return StrategyDecision(
                    strategy=ResponseStrategy.DELAYED,
                    score=0.7,
                    threshold=threshold,
                    urgency=urgency,
                    relevance=relevance,
                    reason="direct_mention_help_seeking_overheated",
                )
            return StrategyDecision(
                strategy=ResponseStrategy.IMMEDIATE,
                score=1.0,
                threshold=threshold,
                urgency=urgency,
                relevance=relevance,
                reason="direct_mention_help_seeking",
            )

        if intent.social_intent == SocialIntent.EMOTIONAL and urgency >= 70:
            # Emotional crisis stays immediate unless severely overheated
            if heat_level == "overheated":
                return StrategyDecision(
                    strategy=ResponseStrategy.DELAYED,
                    score=0.8,
                    threshold=threshold,
                    urgency=urgency,
                    relevance=relevance,
                    reason="emotional_crisis_overheated",
                )
            return StrategyDecision(
                strategy=ResponseStrategy.IMMEDIATE,
                score=0.95,
                threshold=threshold,
                urgency=urgency + 20,
                relevance=relevance,
                reason="emotional_crisis",
            )

        if intent.social_intent == SocialIntent.SILENT and not is_mentioned:
            return StrategyDecision(
                strategy=ResponseStrategy.SILENT,
                score=0.0,
                threshold=threshold,
                urgency=urgency,
                relevance=relevance,
                reason="silent_intent",
            )

        # Direct mention override: being called out always gets immediate response
        if is_mentioned:
            if heat_level == "overheated":
                return StrategyDecision(
                    strategy=ResponseStrategy.DELAYED,
                    score=0.8,
                    threshold=threshold,
                    urgency=urgency,
                    relevance=relevance,
                    reason="direct_mention_overheated",
                )
            return StrategyDecision(
                strategy=ResponseStrategy.IMMEDIATE,
                score=1.0,
                threshold=threshold,
                urgency=urgency,
                relevance=relevance,
                reason="direct_mention",
            )

        # Standard matrix (with higher thresholds)
        if urgency >= 80 and relevance >= 0.7:
            strategy = ResponseStrategy.IMMEDIATE
            reason = "high_urgency_high_relevance"
        elif urgency >= 50 and relevance >= 0.55:
            strategy = ResponseStrategy.DELAYED
            reason = "medium_urgency_delayed"
        elif urgency >= 25 and relevance >= 0.5:
            strategy = ResponseStrategy.DELAYED
            reason = "low_urgency_delayed"
        else:
            strategy = ResponseStrategy.SILENT
            reason = "below_threshold"

        score = (urgency / 100.0) * 0.6 + relevance * 0.4

        return StrategyDecision(
            strategy=strategy,
            score=score,
            threshold=threshold,
            urgency=urgency,
            relevance=relevance,
            reason=reason,
            estimated_delay_seconds=self._estimate_delay(strategy, urgency),
        )

    @staticmethod
    def _estimate_delay(strategy: ResponseStrategy, urgency: float) -> float:
        if strategy == ResponseStrategy.IMMEDIATE:
            return 0.0
        if strategy == ResponseStrategy.DELAYED:
            if urgency >= 70:
                return 15.0
            if urgency >= 40:
                return 30.0
            return 60.0
        return 0.0
