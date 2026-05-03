"""表情包反馈观察器：观察发送后的群友反应，调整偏好。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sirius_chat.skills.sticker.indexer import StickerIndexer
from sirius_chat.skills.sticker.preference import StickerPreferenceManager

logger = logging.getLogger(__name__)

_POSITIVE_SIGNALS = ["哈哈", "笑死", "确实", "太真实了", "保存了", "好图", "绝了", "妙啊", "可以", "不错"]
_NEGATIVE_SIGNALS = ["?", "无语", "尴尬", "冷", "没意思", "不好笑", "算了"]


class StickerFeedbackObserver:
    """观察表情包发送后的群友反应，调整人格偏好。

    在发送表情包后启动观察任务，等待 15 秒后分析后续消息，
    根据反应调整标签成功率和群聊反馈。
    """

    def __init__(
        self,
        indexer: StickerIndexer,
        preference_manager: StickerPreferenceManager,
        basic_memory: Any | None = None,
    ) -> None:
        self._indexer = indexer
        self._preference_manager = preference_manager
        self._basic_memory = basic_memory

    async def observe(
        self,
        sticker_id: str,
        group_id: str,
        sent_at: str,
        wait_seconds: float = 15.0,
    ) -> None:
        """观察发送表情包后的群友反应。

        Args:
            sticker_id: 发送的表情包 ID
            group_id: 群号
            sent_at: 发送时间（ISO 格式）
            wait_seconds: 等待观察的时间（秒）
        """
        await asyncio.sleep(wait_seconds)

        record = self._indexer.get(sticker_id)
        if record is None:
            return

        # 分析后续消息
        positive_count, negative_count = self._analyze_reactions(group_id, sent_at)

        # 更新标签成功率
        success = positive_count > negative_count
        self._preference_manager.update_tag_success(sticker_id, record.tags, success)

        # 更新群聊反馈
        if positive_count > 0 or negative_count > 0:
            self._preference_manager.update_group_feedback(record.tags, positive_count > negative_count)

        # 更新使用记录
        self._preference_manager.record_usage(sticker_id, record.tags)

        # 更新使用次数
        record.usage_count += 1
        record.last_used_at = datetime.now(timezone.utc).isoformat()
        self._indexer.update_record(record)

        logger.info(
            "表情包反馈: %s | 正面=%d, 负面=%d | 成功率更新=%s",
            sticker_id,
            positive_count,
            negative_count,
            success,
        )

    def _analyze_reactions(self, group_id: str, sent_at: str) -> tuple[int, int]:
        """分析发送后的群友反应。

        Returns:
            (positive_count, negative_count)
        """
        if self._basic_memory is None:
            return 0, 0

        try:
            # 获取后续消息
            entries = self._basic_memory.get_entries_after(group_id, sent_at, limit=10)
            if not entries:
                return 0, 0

            positive = 0
            negative = 0

            for entry in entries:
                content = getattr(entry, "content", "") or ""
                content_lower = content.lower()

                for signal in _POSITIVE_SIGNALS:
                    if signal in content_lower:
                        positive += 1
                        break

                for signal in _NEGATIVE_SIGNALS:
                    if signal in content_lower:
                        negative += 1
                        break

            return positive, negative
        except Exception as exc:
            logger.warning("分析表情包反馈失败: %s", exc)
            return 0, 0

    async def update_novelty_scores(self) -> None:
        """更新所有表情包的新鲜度分数。

        每天衰减一次。
        """
        now = datetime.now(timezone.utc)
        for record in self._indexer.list_all():
            try:
                discovered = datetime.fromisoformat(record.discovered_at.replace("Z", "+00:00"))
                days_since_discovery = (now - discovered).days

                last_used = None
                if record.last_used_at:
                    last_used = datetime.fromisoformat(record.last_used_at.replace("Z", "+00:00"))
                days_since_used = (now - last_used).days if last_used else days_since_discovery

                # 新鲜度衰减公式
                # 新发现的表情包新鲜度高，随时间衰减
                # 被使用过的表情包衰减更快
                base_decay = 0.95 ** days_since_discovery
                usage_decay = 0.9 ** record.usage_count
                time_decay = 0.98 ** days_since_used

                record.novelty_score = max(0.1, base_decay * usage_decay * time_decay)
                self._indexer.update_record(record)
            except Exception as exc:
                logger.warning("更新新鲜度失败 %s: %s", record.sticker_id, exc)

        logger.info("更新 %d 个表情包的新鲜度", len(self._indexer.list_all()))
