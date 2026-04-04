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
        if self._queue:
            return self._queue.popleft()
        return "[mock] no configured response"
