"""参与决策协调器

协调热度分析和意图分析子系统，输出最终的参与决策。


决策逻辑：
1. 热度分析（零开销）→ 群聊是否过热
2. 意图分析（LLM 或关键词回退）→ 消息是否指向 AI
3. 综合决策：热度 × 意图 × sensitivity → should_reply
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer
from sirius_chat.core.intent_v2 import IntentAnalysis

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EngagementDecision:
    """参与决策结果。"""
    should_reply: bool
    engagement_score: float    # 0.0-1.0 综合参与意愿
    reason: str                # 人类可读的决策理由
    heat: HeatAnalysis | None = None
    intent: IntentAnalysis | None = None


class EngagementCoordinator:
    """协调热度与意图分析，输出最终参与决策。"""

    @staticmethod
    def decide(
        *,
        intent: IntentAnalysis | None,
        heat: HeatAnalysis,
        sensitivity: float = 0.5,
        event_hit: bool = False,
    ) -> EngagementDecision:
        """综合热度与意图做出参与决策。

        Args:
            intent: 意图分析结果（None 表示分析未执行）。
            heat: 热度分析结果。
            sensitivity: 参与敏感度 0.0(克制) - 1.0(积极)。
            event_hit: 是否命中了相关事件记忆。
        """
        sensitivity = max(0.0, min(1.0, sensitivity))

        if intent is not None and intent.force_no_reply:
            return EngagementDecision(
                should_reply=False,
                engagement_score=0.0,
                reason=f"意图规则抑制回复: {intent.reason}",
                heat=heat,
                intent=intent,
            )

        # ── 直接指向当前模型自身的消息 → 高概率回复 ──
        if intent is not None and intent.directed_at_current_ai:
            # 即便群聊过热，被直接点名时仍应回复
            engagement = _compute_directed_engagement(intent, heat, sensitivity)
            should_reply = engagement >= 0.35
            reason = (
                f"消息指向当前模型 (target={intent.target}, scope={intent.target_scope}), "
                f"engagement={engagement:.2f}"
            )
            return EngagementDecision(
                should_reply=should_reply,
                engagement_score=engagement,
                reason=reason,
                heat=heat,
                intent=intent,
            )

        if intent is not None and intent.target_scope == "other_ai":
            engagement = 0.03 + sensitivity * 0.06
            if event_hit:
                engagement += 0.05
            engagement = min(1.0, engagement)
            should_reply = False
            return EngagementDecision(
                should_reply=should_reply,
                engagement_score=engagement,
                reason=f"消息更像是在对其他AI说话 (scope=other_ai), engagement={engagement:.2f}",
                heat=heat,
                intent=intent,
            )

        # ── 明确指向他人 → 低概率回复 ──
        if intent is not None and intent.target == "others":
            # 只有在非常积极的 sensitivity 下才会插话
            engagement = 0.05 + sensitivity * 0.10
            if event_hit:
                engagement += 0.10
            engagement = min(1.0, engagement)
            should_reply = engagement >= 0.50
            return EngagementDecision(
                should_reply=should_reply,
                engagement_score=engagement,
                reason=f"消息指向其他参与者 (target=others), engagement={engagement:.2f}",
                heat=heat,
                intent=intent,
            )

        # ── 面向全体或目标不明 → 由热度和 sensitivity 主导 ──
        engagement = _compute_ambient_engagement(
            intent=intent,
            heat=heat,
            sensitivity=sensitivity,
            event_hit=event_hit,
        )

        # 决策阈值：基于 sensitivity 动态调整
        #   sensitivity=0.0 → threshold=0.60 (非常克制)
        #   sensitivity=0.5 → threshold=0.45 (平衡)
        #   sensitivity=1.0 → threshold=0.30 (积极)
        threshold = 0.60 - sensitivity * 0.30
        should_reply = engagement >= threshold

        target_label = intent.target if intent else "N/A"
        return EngagementDecision(
            should_reply=should_reply,
            engagement_score=engagement,
            reason=(
                f"ambient decision: target={target_label}, "
                f"heat={heat.heat_level}({heat.heat_score:.2f}), "
                f"engagement={engagement:.2f}, threshold={threshold:.2f}"
            ),
            heat=heat,
            intent=intent,
        )

    @staticmethod
    def check_reply_frequency_limit(
        *,
        assistant_reply_timestamps: list[str],
        now: datetime,
        window_seconds: float,
        max_replies: int,
        exempt_on_mention: bool,
        is_mentioned: bool,
    ) -> bool:
        """检查回复频率限制。返回 True 表示应限流。"""
        if max_replies <= 0 or window_seconds <= 0:
            return False

        cutoff = now.timestamp() - window_seconds
        recent_count = 0
        for raw_ts in assistant_reply_timestamps:
            try:
                parsed = datetime.fromisoformat(raw_ts.strip())
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed.timestamp() >= cutoff:
                    recent_count += 1
            except (ValueError, TypeError):
                continue

        if recent_count < max_replies:
            return False

        if exempt_on_mention and is_mentioned:
            return False

        logger.info(
            "回复频率已达上限（%d/%d），稍作等待再开口",
            recent_count, max_replies,
        )
        return True


def _compute_directed_engagement(
    intent: IntentAnalysis,
    heat: HeatAnalysis,
    sensitivity: float,
) -> float:
    """被直接提及时的参与度计算。"""
    base = 0.70 + sensitivity * 0.20  # 0.70-0.90

    # 问题或请求时进一步加分
    if intent.intent_type in ("question", "request", "command"):
        base += 0.10

    # 群聊过热时轻微降低，但不会低于 0.50
    if heat.heat_level == "overheated":
        base -= 0.10
    elif heat.heat_level == "hot":
        base -= 0.05

    return max(0.0, min(1.0, base))


def _compute_ambient_engagement(
    *,
    intent: IntentAnalysis | None,
    heat: HeatAnalysis,
    sensitivity: float,
    event_hit: bool,
) -> float:
    """面向全体或目标不明时的参与度计算。

    核心原则：热度越高 → 参与度越低（避免刷屏）。
    """
    # 基础分：由 sensitivity 主导
    base = 0.20 + sensitivity * 0.30  # 0.20-0.50

    # 热度惩罚
    heat_penalties = {
        "cold": 0.0,
        "warm": -0.05,
        "hot": -0.15,
        "overheated": -0.30,
    }
    base += heat_penalties.get(heat.heat_level, -0.10)

    # 意图加分
    if intent is not None:
        if intent.intent_type in ("question", "request", "command"):
            base += 0.15
        elif intent.intent_type == "reaction":
            base -= 0.10
        # target=unknown 且有代词可能指向 AI
        if intent.target == "unknown" and intent.importance > 0.5:
            base += 0.08

    # 事件相关加分
    if event_hit:
        base += 0.10

    return max(0.0, min(1.0, base))


__all__ = ["EngagementDecision", "EngagementCoordinator"]
