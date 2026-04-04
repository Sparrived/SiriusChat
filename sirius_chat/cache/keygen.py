"""Cache key generation utilities."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def generate_cache_key(
    prefix: str,
    model: str,
    content: str | dict[str, Any],
    *,
    include_temperature: bool = False,
    temperature: float | None = None,
) -> str:
    """Generate a deterministic cache key.
    
    Args:
        prefix: Cache key prefix (e.g., "provider", "embedding")
        model: Model name
        content: Request content (string or dict)
        include_temperature: Whether to include temperature in key
        temperature: Model temperature
        
    Returns:
        Hex-encoded SHA256 hash prefixed with prefix and model
    """
    # Convert content to JSON string if dict
    if isinstance(content, dict):
        content_str = json.dumps(content, separators=(",", ":"), sort_keys=True)
    else:
        content_str = str(content)
    
    # Build key components
    components = [content_str]
    if include_temperature and temperature is not None:
        components.append(str(round(temperature, 2)))
    
    # Hash the combined content
    combined = "|".join(components)
    content_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    # Return formatted key
    return f"{prefix}:{model}:{content_hash}"


def generate_generation_request_key(
    model: str,
    messages: list[dict[str, str]],
    system_prompt: str,
    *,
    include_temperature: bool = False,
    temperature: float | None = None,
) -> str:
    """Generate cache key for a generation request.
    
    Args:
        model: Model name
        messages: Chat messages
        system_prompt: System prompt
        include_temperature: Whether to include temperature
        temperature: Model temperature
        
    Returns:
        Cache key
    """
    request_content = {
        "system_prompt": system_prompt,
        "messages": messages,
    }
    return generate_cache_key(
        "gen_req",
        model,
        request_content,
        include_temperature=include_temperature,
        temperature=temperature,
    )
