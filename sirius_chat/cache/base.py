"""Abstract base classes and interfaces for caching."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CacheBackend(ABC):
    """Abstract base class for cache backends."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get a value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        ...

    @abstractmethod
    async def set(
        self, key: str, value: Any, ttl: int | None = None
    ) -> None:
        """Set a value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds. None means no expiration.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a value from cache.
        
        Args:
            key: Cache key
        """
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all values from cache."""
        ...

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats (hits, misses, size, etc.)
        """
        ...

    async def set_many(
        self, items: dict[str, Any], ttl: int | None = None
    ) -> None:
        """Set multiple values in cache.
        
        Args:
            items: Dictionary of key-value pairs
            ttl: Time-to-live in seconds
        """
        for key, value in items.items():
            await self.set(key, value, ttl)

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Get multiple values from cache.
        
        Args:
            keys: List of cache keys
            
        Returns:
            Dictionary of key-value pairs (only for found keys)
        """
        result = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                result[key] = value
        return result
