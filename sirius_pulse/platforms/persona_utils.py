"""Small helpers shared by persona-related platform code."""

from __future__ import annotations


def extract_json(text: str) -> str:
    """Remove an optional Markdown code fence around a JSON payload."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
