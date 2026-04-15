from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class GenerationRequest:
    model: str
    system_prompt: str
    messages: list[dict[str, object]]
    temperature: float = 0.7
    max_tokens: int = 512
    timeout_seconds: float | None = None
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


def build_generation_debug_context(
    request: GenerationRequest,
    *,
    provider_name: str,
    url: str = "",
    base_url: str = "",
    timeout_seconds: float | None = None,
    method: str = "POST",
) -> dict[str, object]:
    """Build structured debug metadata for upstream provider calls."""
    estimated_input_tokens = estimate_generation_request_input_tokens(request)
    estimated_total_upper = estimated_input_tokens + max(0, int(request.max_tokens))

    multimodal_message_count = 0
    multimodal_part_count = 0
    text_part_count = 0
    for msg in request.messages:
        content = msg.get("content", "")
        if not isinstance(content, list):
            continue
        multimodal_message_count += 1
        multimodal_part_count += len(content)
        text_part_count += sum(
            1
            for part in content
            if isinstance(part, dict) and str(part.get("type", "")).strip() == "text"
        )

    return {
        "provider": provider_name,
        "method": method,
        "url": url,
        "base_url": base_url,
        "timeout_seconds": timeout_seconds,
        "purpose": request.purpose,
        "model": request.model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "input_message_count": len(request.messages),
        "total_message_count": len(request.messages) + (1 if request.system_prompt else 0),
        "multimodal_message_count": multimodal_message_count,
        "multimodal_part_count": multimodal_part_count,
        "multimodal_text_part_count": text_part_count,
        "has_system_prompt": bool(request.system_prompt),
        "system_prompt_chars": len(request.system_prompt),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_total_token_upper_bound": estimated_total_upper,
    }


def resolve_generation_timeout_seconds(
    request: GenerationRequest,
    default_timeout_seconds: float,
) -> float:
    """Return the effective timeout for a provider call.

    Request-scoped timeout overrides provider defaults when supplied.
    """
    timeout_seconds = request.timeout_seconds
    if timeout_seconds is None:
        timeout_seconds = default_timeout_seconds
    resolved_timeout = float(timeout_seconds)
    if resolved_timeout <= 0:
        raise ValueError("GenerationRequest.timeout_seconds must be greater than 0.")
    return resolved_timeout


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, request: GenerationRequest) -> str:
        """Generate one assistant message from the upstream provider."""
        ...


@runtime_checkable
class AsyncLLMProvider(Protocol):
    async def generate_async(self, request: GenerationRequest) -> str:
        """Generate one assistant message asynchronously from the upstream provider."""
        ...
