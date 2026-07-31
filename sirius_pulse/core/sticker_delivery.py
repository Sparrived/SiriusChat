"""Helpers for delivering sticker choices after text replies."""

from __future__ import annotations


def dedupe_sticker_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result
