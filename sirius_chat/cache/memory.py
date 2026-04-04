"""LRU memory cache implementation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from sirius_chat.cache.base import CacheBackend


class MemoryCache(CacheBackend):
    """In-memory LRU cache with optional TTL support."""

    def __init__(self, max_size: int = 1000) -> None:
        """Initialize memory cache.
        
        Args:
            max_size: Maximum number of items to store
        """
        self.max_size = max_size
        self._cache: OrderedDict[str, tuple[Any, datetime | None]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any | None:
        """Get a value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            value, expiry = self._cache[key]
            
            # Check expiration
            if expiry is not None and datetime.now() >= expiry:
                del self._cache[key]
                self._misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    async def set(
        self, key: str, value: Any, ttl: int | None = None
    ) -> None:
        """Set a value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds
        """
        async with self._lock:
            # Calculate expiry if TTL provided
            expiry = None
            if ttl is not None:
                expiry = datetime.now() + timedelta(seconds=ttl)

            # Remove old entry if exists (to update position)
            if key in self._cache:
                del self._cache[key]

            # Add new entry
            self._cache[key] = (value, expiry)

            # Enforce max size with LRU eviction
            if len(self._cache) > self.max_size:
                # Remove least recently used (first item)
                self._cache.popitem(last=False)

    async def delete(self, key: str) -> None:
        """Delete a value from cache.
        
        Args:
            key: Cache key
        """
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear all values from cache."""
        async with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        total_requests = self._hits + self._misses
        hit_rate = (
            self._hits / total_requests if total_requests > 0 else 0.0
        )
        return {
            "type": "memory_lru",
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "total_requests": total_requests,
        }

    def size_bytes(self) -> int:
        """Estimate cache size in bytes.
        
        Returns:
            Approximate size in bytes (rough estimate)
        """
        # Rough estimate: 100 bytes per entry + value size
        total = 0
        for value, _ in self._cache.values():
            entry_size = 100  # Base overhead
            try:
                if isinstance(value, str):
                    entry_size += len(value.encode())
                elif isinstance(value, (dict, list)):
                    import json
                    entry_size += len(json.dumps(value).encode())
            except Exception:
                pass
            total += entry_size
        return total
