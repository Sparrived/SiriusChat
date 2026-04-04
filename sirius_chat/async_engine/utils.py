"""Utility functions for async engine operations."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sirius_chat.models import Transcript


def build_event_hit_system_note(*, speaker: str, hit_payload: dict[str, object]) -> str:
    """Build a system note describing an event memory hit."""
    level = str(hit_payload.get("level", "new"))
    entry = hit_payload.get("entry")
    raw_score = hit_payload.get("score", 0.0)
    if isinstance(raw_score, (int, float)):
        score = float(raw_score)
    else:
        score = 0.0
    if entry is None:
        return f"事件记忆新增[{speaker}]：未生成有效事件摘要。"

    event_id = str(getattr(entry, "event_id", ""))
    summary = str(getattr(entry, "summary", "")).strip() or "未提供"
    if level == "high":
        return f"事件记忆命中[{speaker}]：高置信命中#{event_id} (score={score:.2f}) | 摘要: {summary}"
    if level == "weak":
        return f"事件记忆命中[{speaker}]：弱命中#{event_id} (score={score:.2f}) | 摘要: {summary}"
    return f"事件记忆新增[{speaker}]：#{event_id} | 摘要: {summary}"


def record_task_stat(
    transcript: Transcript, task_name: str, metric: str, increment: int = 1
) -> None:
    """Record a task statistic in the transcript."""
    task_stats = transcript.orchestration_stats.setdefault(task_name, {})
    task_stats[metric] = task_stats.get(metric, 0) + increment


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in text using a coarse heuristic.
    
    This is a cheap, deterministic estimate suitable for budget guardrails.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def extract_json_payload(raw: str) -> dict[str, object] | None:
    """Extract JSON object from raw text, handling partial JSON."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = raw[start : end + 1]
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def normalize_multimodal_inputs(
    multimodal_inputs: list[dict[str, str]],
    *,
    max_items: int,
    max_value_length: int,
    supported_types: set[str] | None = None,
) -> list[dict[str, str]]:
    """Normalize and validate multimodal inputs.
    
    Args:
        multimodal_inputs: Raw list of multimodal input dicts
        max_items: Maximum number of items to keep
        max_value_length: Maximum length for each value
        supported_types: Set of supported media types. Defaults to 
                        {"image", "video", "audio", "text"}
    """
    if supported_types is None:
        supported_types = {"image", "video", "audio", "text"}
    
    normalized: list[dict[str, str]] = []
    for item in multimodal_inputs[:max_items]:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("type", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        if not media_type or not value:
            continue
        if media_type not in supported_types:
            continue
        if len(value) > max_value_length:
            value = value[:max_value_length]
        normalized.append({"type": media_type, "value": value})
    return normalized
