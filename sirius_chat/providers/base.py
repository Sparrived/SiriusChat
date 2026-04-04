from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class GenerationRequest:
    model: str
    system_prompt: str
    messages: list[dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 512


class LLMProvider(Protocol):
    def generate(self, request: GenerationRequest) -> str:
        """Generate one assistant message from the upstream provider."""


class AsyncLLMProvider(Protocol):
    async def generate_async(self, request: GenerationRequest) -> str:
        """Generate one assistant message asynchronously from the upstream provider."""
