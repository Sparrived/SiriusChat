from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class GenerationRequest:
    model: str
    system_prompt: str
    messages: list[dict[str, object]]
    temperature: float = 0.7
    max_tokens: int = 512
    purpose: str = "chat_main"


def estimate_generation_request_input_tokens(request: GenerationRequest) -> int:
    """Estimate input tokens for logging and budget visibility.

    Uses a coarse deterministic heuristic: ~1 token per 4 characters.
    """
    text_parts = [request.system_prompt]
    for msg in request.messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts.extend(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
            continue
        text_parts.append(str(content))
    merged = "\n".join(part for part in text_parts if part)
    if not merged:
        return 0
    return max(1, (len(merged) + 3) // 4)


class LLMProvider(Protocol):
    def generate(self, request: GenerationRequest) -> str:
        """Generate one assistant message from the upstream provider."""


class AsyncLLMProvider(Protocol):
    async def generate_async(self, request: GenerationRequest) -> str:
        """Generate one assistant message asynchronously from the upstream provider."""
