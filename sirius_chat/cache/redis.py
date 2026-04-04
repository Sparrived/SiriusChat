"""Redis cache backend implementation."""

from __future__ import annotations

import json
import logging
from typing import Any

from sirius_chat.cache.base import CacheBackend

logger = logging.getLogger(__name__)


class RedisCache(CacheBackend):
    """Redis-backed distributed cache.
    
    Requires: redis package must be installed
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        prefix: str = "sirius:",
    ) -> None:
        """Initialize Redis cache.
        
        Args:
            redis_url: Redis connection URL
            prefix: Key prefix for all cache entries
        """
        self.redis_url = redis_url
        self.prefix = prefix
        self._redis: Any = None
        self._initialized = False

    async def _ensure_connected(self) -> None:
        """Ensure Redis connection is established."""
        if self._initialized:
            return

        try:
            import redis.asyncio as redis
            self._redis = redis.from_url(self.redis_url, decode_responses=True)
            # Test connection
            await self._redis.ping()
            logger.info(f"Connected to Redis: {self.redis_url}")
            self._initialized = True
        except ImportError:
            raise ImportError(
                "redis package required for RedisCache. "
                "Install with: pip install redis"
            )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> Any | None:
        """Get a value from Redis cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        await self._ensure_connected()
        try:
            value = await self._redis.get(self._make_key(key))
            if value is None:
                return None
            # Deserialize JSON
            return json.loads(value)
        except Exception as e:
            logger.warning(f"Failed to get key {key} from Redis: {e}")
            return None

    async def set(
        self, key: str, value: Any, ttl: int | None = None
    ) -> None:
        """Set a value in Redis cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds
        """
        await self._ensure_connected()
        try:
            # Serialize to JSON
            json_value = json.dumps(value)
            redis_key = self._make_key(key)
            if ttl is None:
                await self._redis.set(redis_key, json_value)
            else:
                await self._redis.setex(redis_key, ttl, json_value)
        except Exception as e:
            logger.warning(f"Failed to set key {key} in Redis: {e}")

    async def delete(self, key: str) -> None:
        """Delete a value from Redis cache.
        
        Args:
            key: Cache key
        """
        await self._ensure_connected()
        try:
            await self._redis.delete(self._make_key(key))
        except Exception as e:
            logger.warning(f"Failed to delete key {key} from Redis: {e}")

    async def clear(self) -> None:
        """Clear all values with the cache prefix."""
        await self._ensure_connected()
        try:
            # Get all keys with prefix and delete them
            pattern = f"{self.prefix}*"
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern)
                if keys:
                    await self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning(f"Failed to clear Redis cache: {e}")

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache info
        """
        if not self._initialized:
            return {"type": "redis", "status": "not_connected"}

        return {
            "type": "redis",
            "url": self.redis_url,
            "prefix": self.prefix,
            "status": "connected",
        }

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            try:
                await self._redis.close()
                self._initialized = False
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")
