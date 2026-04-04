"""Caching layer for Sirius Chat.

Provides flexible caching backends for Provider responses with support for
in-memory LRU caching and optional Redis integration.
"""

from __future__ import annotations

from sirius_chat.cache.base import CacheBackend
from sirius_chat.cache.keygen import (
    generate_cache_key,
    generate_generation_request_key,
)
from sirius_chat.cache.memory import MemoryCache
from sirius_chat.cache.redis import RedisCache

__all__ = [
    "CacheBackend",
    "MemoryCache",
    "RedisCache",
    "generate_cache_key",
    "generate_generation_request_key",
]
