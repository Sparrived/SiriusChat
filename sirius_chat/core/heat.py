"""群聊热度分析子系统

通过实时参与人数、消息频率、AI参与比例等维度评估群聊热度，
热度越高AI越应该克制参与，避免在多人活跃聊天时过度刷屏。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True)
class HeatAnalysis:
    """热度分析结果。"""
    heat_level: str          # cold | warm | hot | overheated
    heat_score: float        # 0.0-1.0 综合热度分
    active_participants: int # 近期活跃参与者数
    messages_per_minute: float  # 消息频率
    ai_participation_ratio: float  # AI发言占比 (0-1)


def _classify_heat_level(score: float) -> str:
    if score < 0.25:
        return "cold"
    if score < 0.50:
        return "warm"
    if score < 0.75:
        return "hot"
    return "overheated"


class HeatAnalyzer:
    """分析群聊实时热度。

    基于滑动窗口内的消息统计推导热度，不调用 LLM，零额外开销。
    """

    @staticmethod
    def analyze(
        *,
        group_recent_count: int,
        window_seconds: float,
        active_participant_ids: set[str],
        assistant_reply_count_in_window: int,
    ) -> HeatAnalysis:
        """分析当前群聊热度。

        Args:
            group_recent_count: 滑动窗口内群聊总消息数（含所有角色）。
            window_seconds: 滑动窗口长度（秒），通常 30-60。
            active_participant_ids: 窗口内发言过的唯一参与者 ID 集合。
            assistant_reply_count_in_window: 窗口内 AI 回复数。
        """
        active_participants = len(active_participant_ids)
        total = max(1, group_recent_count)

        # 消息频率：条/分钟
        minutes = max(window_seconds / 60.0, 0.1)
        messages_per_minute = total / minutes

        # AI 参与占比
        ai_participation_ratio = (
            assistant_reply_count_in_window / total if total > 0 else 0.0
        )

        # ── 综合热度分 ──
        # 1) 密度分：基于消息频率
        #    0 msg/min → 0.0; 6+ msg/min → 1.0
        density_score = min(1.0, messages_per_minute / 6.0)

        # 2) 多人分：基于活跃人数
        #    1 人 → 0.0; 5+ 人 → 1.0
        crowd_score = min(1.0, max(0, active_participants - 1) / 4.0)

        # 3) AI 已参与过多时热度上升
        ai_overshare_score = min(1.0, max(0.0, ai_participation_ratio - 0.3) / 0.4)

        heat_score = (
            0.45 * density_score
            + 0.35 * crowd_score
            + 0.20 * ai_overshare_score
        )
        heat_score = max(0.0, min(1.0, heat_score))

        return HeatAnalysis(
            heat_level=_classify_heat_level(heat_score),
            heat_score=heat_score,
            active_participants=active_participants,
            messages_per_minute=round(messages_per_minute, 2),
            ai_participation_ratio=round(ai_participation_ratio, 3),
        )


__all__ = ["HeatAnalysis", "HeatAnalyzer"]
