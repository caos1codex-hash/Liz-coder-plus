"""Tests for EmbeddingCache."""

import threading
import pytest

from multiagent.semantic.cache import EmbeddingCache
from multiagent.semantic.embedding import DummyEmbeddingProvider, EmbeddingResult


class TestEmbeddingCache:
    @pytest.fixture
    def cache(self):
        return EmbeddingCache(max_size=10)

    @pytest.fixture
    def provider(self):
        return DummyEmbeddingProvider(dims=16)

    @pytest.mark.asyncio
    async def test_put_and_get(self, cache, provider):
        result = await provider.embed_text("test")
        cache.put(result.text_hash, result)
        cached = cache.get(result.text_hash)
        assert cached is not None
        assert cached.vector == result.vector
        assert cached.provider == result.provider

    def test_get_miss(self, cache):
        result = cache.get("nonexistent_hash")
        assert result is None

    @pytest.mark.asyncio
    async def test_put_updates_existing(self, cache, provider):
        r1 = await provider.embed_text("text")
        cache.put(r1.text_hash, r1)

        # Same hash, different result
        r2 = EmbeddingResult(
            vector=[0.5] * provider.dimensions(),
            text_hash=r1.text_hash,
            provider="updated",
        )
        cache.put(r2.text_hash, r2)

        cached = cache.get(r1.text_hash)
        assert cached is not None
        assert cached.provider == "updated"

    def test_remove(self, cache):
        cache.put("hash1", EmbeddingResult(vector=[1.0]))
        assert cache.remove("hash1") is True
        assert cache.get("hash1") is None

    def test_remove_nonexistent(self, cache):
        assert cache.remove("nonexistent") is False

    def test_clear(self, cache):
        for i in range(5):
            cache.put(f"hash-{i}", EmbeddingResult(vector=[float(i)]))
        count = cache.clear()
        assert count == 5
        assert cache.size == 0

    def test_size(self, cache):
        assert cache.size == 0
        cache.put("h1", EmbeddingResult(vector=[1.0]))
        assert cache.size == 1
        cache.put("h2", EmbeddingResult(vector=[2.0]))
        assert cache.size == 2

    def test_lru_eviction(self, cache):
        """Least recently used entry should be evicted when full."""
        max_size = cache.max_size
        for i in range(max_size):
            cache.put(f"hash-{i}", EmbeddingResult(vector=[float(i)]))

        assert cache.size == max_size

        # Add one more — should evict the oldest
        cache.put("hash-new", EmbeddingResult(vector=[99.0]))
        assert cache.size == max_size
        assert cache.get("hash-0") is None  # First entry evicted
        assert cache.get("hash-new") is not None

    def test_lru_order_updated_on_get(self, cache):
        """Getting an entry should update its LRU position."""
        for i in range(10):
            cache.put(f"hash-{i}", EmbeddingResult(vector=[float(i)]))

        # Access hash-0 to make it recently used
        cache.get("hash-0")

        # Add new entry — should evict hash-1 (not hash-0)
        cache.put("hash-new", EmbeddingResult(vector=[99.0]))
        assert cache.get("hash-0") is not None
        assert cache.get("hash-1") is None

    def test_hit_miss_counters(self, cache):
        cache.put("h1", EmbeddingResult(vector=[1.0]))

        cache.get("h1")  # hit
        cache.get("h1")  # hit
        cache.get("h2")  # miss
        cache.get("h3")  # miss

        assert cache.hits == 2
        assert cache.misses == 2

    def test_hit_rate(self, cache):
        cache.put("h1", EmbeddingResult(vector=[1.0]))

        cache.get("h1")  # hit
        cache.get("h2")  # miss
        cache.get("h1")  # hit

        assert abs(cache.hit_rate - (2 / 3)) < 1e-9

    def test_hit_rate_empty(self, cache):
        assert cache.hit_rate == 0.0

    def test_contains(self, cache):
        cache.put("h1", EmbeddingResult(vector=[1.0]))
        assert cache.contains("h1") is True
        assert cache.contains("h2") is False

    def test_contains_does_not_affect_lru(self, cache):
        for i in range(10):
            cache.put(f"h-{i}", EmbeddingResult(vector=[float(i)]))

        # contains should NOT update LRU
        cache.contains("h-0")
        cache.put("new", EmbeddingResult(vector=[99.0]))

        # h-0 should have been evicted (not recently used)
        assert cache.get("h-0") is None

    @pytest.mark.asyncio
    async def test_get_many(self, cache, provider):
        r1 = await provider.embed_text("text1")
        r2 = await provider.embed_text("text2")
        cache.put(r1.text_hash, r1)
        cache.put(r2.text_hash, r2)

        cached, missing = cache.get_many([r1.text_hash, r2.text_hash, "nonexistent"])
        assert len(cached) == 2
        assert len(missing) == 1
        assert missing == ["nonexistent"]

    def test_put_many(self, cache):
        items = [
            ("h1", EmbeddingResult(vector=[1.0])),
            ("h2", EmbeddingResult(vector=[2.0])),
            ("h3", EmbeddingResult(vector=[3.0])),
        ]
        cache.put_many(items)
        assert cache.size == 3

    def test_stats(self, cache):
        cache.put("h1", EmbeddingResult(vector=[1.0]))
        cache.get("h1")  # hit
        cache.get("h2")  # miss

        stats = cache.stats()
        assert stats["size"] == 1
        assert stats["max_size"] == 10
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert "hit_rate" in stats
        assert "evictions" in stats
        assert "utilization" in stats

    def test_reset_stats(self, cache):
        cache.put("h1", EmbeddingResult(vector=[1.0]))
        cache.get("h1")
        cache.get("h2")

        cache.reset_stats()
        assert cache.hits == 0
        assert cache.misses == 0
        assert cache.evictions == 0
        # But cache content is preserved
        assert cache.size == 1

    def test_max_size_property(self, cache):
        assert cache.max_size == 10

    def test_eviction_counter(self, cache):
        for i in range(15):  # max_size=10, so 5 evictions
            cache.put(f"h-{i}", EmbeddingResult(vector=[float(i)]))
        assert cache.evictions == 5

    def test_thread_safety(self, cache):
        """Concurrent access should not corrupt the cache."""
        errors = []

        def writer():
            try:
                for i in range(100):
                    cache.put(f"t-{i}", EmbeddingResult(vector=[float(i)]))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(100):
                    cache.get(f"t-{i % 20}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0