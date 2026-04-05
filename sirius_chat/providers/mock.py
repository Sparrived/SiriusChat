from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field

from sirius_chat.providers.base import GenerationRequest, LLMProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MockProvider(LLMProvider):
    """Deterministic provider for unit tests and local dry runs."""

    responses: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queue = deque(self.responses)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        # 基础调用日志（INFO）
        msg_count = len(request.messages)

        logger.info(
            f"[模型调用] mock-{request.model} | 温度: {request.temperature}, Token上限: {request.max_tokens} "
            f"| 消息数: {msg_count}"
        )
        debug_input = {
            "system_prompt": request.system_prompt,
            "messages": request.messages,
        }
        logger.debug(
            f"[模型调用详情] mock-{request.model} | 完整输入:\n"
            f"{json.dumps(debug_input, ensure_ascii=False, indent=2)}"
        )
        
        self.requests.append(request)
        # 检测事件验证请求并返回有效的 JSON
        is_event_verification = (
            "对话分析专家" in request.system_prompt or 
            "分析这段对话中的潜在事件" in str(request.messages)
        )
        if is_event_verification:
            response = """{
                "record": "是",
                "reason": "测试事件",
                "summary": "测试事件摘要",
                "keywords": ["关键词"],
                "role_slots": ["角色"],
                "time_hints": ["时间"],
                "emotion_tags": ["情绪"]
            }"""
            logger.info(f"[模型调用成功] mock-{request.model} | 字数: {len(response)}")
            logger.debug(f"[模型输出] mock-{request.model} | 响应内容:\n{response}")
            return response
        if self._queue:
            response = self._queue.popleft()
            logger.info(f"[模型调用成功] mock-{request.model} | 字数: {len(response)}")
            logger.debug(f"[模型输出] mock-{request.model} | 响应内容:\n{response}")
            return response
        logger.warning(f"[模型调用] mock-{request.model} | 无配置响应")
        return "[mock] no configured response"
