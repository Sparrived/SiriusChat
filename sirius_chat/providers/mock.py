from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from sirius_chat.providers.base import GenerationRequest, LLMProvider


@dataclass(slots=True)
class MockProvider(LLMProvider):
    """Deterministic provider for unit tests and local dry runs."""

    responses: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queue = deque(self.responses)
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        # 检测事件验证请求并返回有效的 JSON
        is_event_verification = (
            "对话分析专家" in request.system_prompt or 
            "分析这段对话中的潜在事件" in str(request.messages)
        )
        if is_event_verification:
            return """{
                "record": "是",
                "reason": "测试事件",
                "summary": "测试事件摘要",
                "keywords": ["关键词"],
                "role_slots": ["角色"],
                "time_hints": ["时间"],
                "emotion_tags": ["情绪"]
            }"""
        if self._queue:
            return self._queue.popleft()
        return "[mock] no configured response"
