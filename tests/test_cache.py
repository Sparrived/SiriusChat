"""Tests for cache module."""

from __future__ import annotations

import asyncio
import pytest

from sirius_chat.cache import (
    CacheBackend,
    MemoryCache,
    generate_cache_key,
    generate_generation_request_key,
)


class TestMemoryCache:
    """Test MemoryCache implementation."""

    @pytest.fixture
    def cache(self) -> MemoryCache:
        """Create a MemoryCache instance."""
        return MemoryCache(max_size=3)

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache: MemoryCache) -> None:
        """Test basic set and get operations."""
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_key(self, cache: MemoryCache) -> None:
        """Test getting a non-existent key."""
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, cache: MemoryCache) -> None:
        """Test deletion."""
        await cache.set("key1", "value1")
        await cache.delete("key1")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_clear(self, cache: MemoryCache) -> None:
        """Test clearing the cache."""
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.clear()
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self, cache: MemoryCache) -> None:
        """Test LRU eviction when max size exceeded."""
        # max_size=3
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")
        
        # Adding 4th item should evict key1 (least recently used)
        await cache.set("key4", "value4")
        
        assert await cache.get("key1") is None  # Evicted
        assert await cache.get("key2") == "value2"
        assert await cache.get("key3") == "value3"
        assert await cache.get("key4") == "value4"

    @pytest.mark.asyncio
    async def test_lru_access_updates_order(self, cache: MemoryCache) -> None:
        """Test that accessing an item updates LRU order."""
        # max_size=3
        await cache.set("key1", "value1")
        await cache.set("key2", "value2")
        await cache.set("key3", "value3")
        
        # Access key1 to mark it as recently used
        await cache.get("key1")
        
        # Adding 4th item should evict key2 (now least recently used)
        await cache.set("key4", "value4")
        
        assert await cache.get("key1") == "value1"  # Still there
        assert await cache.get("key2") is None  # Evicted
        assert await cache.get("key3") == "value3"
        assert await cache.get("key4") == "value4"

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, cache: MemoryCache) -> None:
        """Test TTL expiration."""
        await cache.set("key1", "value1", ttl=1)
        
        # Should be available immediately
        result = await cache.get("key1")
        assert result == "value1"
        
        # Wait for expiration
        await asyncio.sleep(1.1)
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_many_get_many(self, cache: MemoryCache) -> None:
        """Test set_many and get_many operations."""
        items = {"key1": "value1", "key2": "value2", "key3": "value3"}
        await cache.set_many(items)
        
        result = await cache.get_many(["key1", "key2", "key4"])
        assert result == {"key1": "value1", "key2": "value2"}

    @pytest.mark.asyncio
    async def test_stats(self, cache: MemoryCache) -> None:
        """Test statistics tracking."""
        await cache.set("key1", "value1")
        await cache.get("key1")  # Hit
        await cache.get("key2")  # Miss
        
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["hit_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_dict_values(self, cache: MemoryCache) -> None:
        """Test caching dictionary values."""
        value = {"nested": {"data": [1, 2, 3]}}
        await cache.set("key1", value)
        result = await cache.get("key1")
        assert result == value

    @pytest.mark.asyncio
    async def test_size_bytes(self, cache: MemoryCache) -> None:
        """Test size estimation."""
        await cache.set("key1", "value1")
        size = cache.size_bytes()
        assert size > 0


class TestCacheKeyGeneration:
    """Test cache key generation functions."""

    def test_generate_cache_key(self) -> None:
        """Test basic cache key generation."""
        key = generate_cache_key("prefix", "model-v1", "test content")
        assert key.startswith("prefix:model-v1:")
        # SHA256 hash should produce consistent keys
        key2 = generate_cache_key("prefix", "model-v1", "test content")
        assert key == key2

    def test_cache_key_different_content(self) -> None:
        """Test that different content produces different keys."""
        key1 = generate_cache_key("prefix", "model-v1", "content1")
        key2 = generate_cache_key("prefix", "model-v1", "content2")
        assert key1 != key2

    def test_cache_key_with_dict(self) -> None:
        """Test cache key generation with dict content."""
        content = {"type": "request", "data": [1, 2, 3]}
        key = generate_cache_key("prefix", "model-v1", content)
        assert key.startswith("prefix:model-v1:")

    def test_cache_key_with_temperature(self) -> None:
        """Test cache key generation with temperature."""
        key1 = generate_cache_key(
            "prefix", "model-v1", "content",
            include_temperature=True, temperature=0.7
        )
        key2 = generate_cache_key(
            "prefix", "model-v1", "content",
            include_temperature=True, temperature=0.8
        )
        assert key1 != key2

    def test_generation_request_key(self) -> None:
        """Test generation request key generation."""
        messages = [{"role": "user", "content": "Hello"}]
        key = generate_generation_request_key(
            "gpt-4",
            messages,
            "You are helpful"
        )
        assert "gen_req:gpt-4:" in key

    def test_generation_request_key_consistency(self) -> None:
        """Test that same request produces same key."""
        messages = [{"role": "user", "content": "Hello"}]
        key1 = generate_generation_request_key(
            "gpt-4",
            messages,
            "You are helpful"
        )
        key2 = generate_generation_request_key(
            "gpt-4",
            messages,
            "You are helpful"
        )
        assert key1 == key2


class TestCacheStats:
    """Test cache statistics."""

    @pytest.mark.asyncio
    async def test_empty_cache_stats(self) -> None:
        """Test stats for empty cache."""
        cache = MemoryCache()
        stats = cache.stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    @pytest.mark.asyncio
    async def test_hit_rate_calculation(self) -> None:
        """Test hit rate calculation."""
        cache = MemoryCache()
        await cache.set("key1", "value1")
        
        # 2 hits
        await cache.get("key1")
        await cache.get("key1")
        
        # 3 misses
        await cache.get("key2")
        await cache.get("key3")
        await cache.get("key4")
        
        stats = cache.stats()
        assert stats["hit_rate"] == pytest.approx(0.4, rel=0.01)
        assert stats["total_requests"] == 5
