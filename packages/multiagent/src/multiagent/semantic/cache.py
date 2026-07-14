"""Embedding cache — avoids recomputing embeddings for identical text.

Stores embedding results keyed by text hash. Provides hit/miss
statistics and automatic size-based eviction.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from .embedding import EmbeddingResult

logger = logging.getLogger(__name__)


class EmbeddingCache:
    """LRU cache for embedding results.

    Thread-safe. Stores embeddings keyed by SHA-256 text hash.
    Supports configurable max size and automatic eviction.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        """Initialize the cache.

        Args:
            max_size: Maximum number of cached embeddings. When exceeded,
                      the least recently used entry is evicted.
        """
        self._cache: OrderedDict[str, EmbeddingResult] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # -- core operations ---------------------------------------------------

    def get(self, text_hash: str) -> Optional[EmbeddingResult]:
        """Retrieve a cached embedding by text hash.

        Returns the cached EmbeddingResult or None if not found.
        Updates LRU order on hit.
        """
        with self._lock:
            if text_hash in self._cache:
                # Move to end (most recently used)
                self._cache.move_to_end(text_hash)
                self._hits += 1
                return self._cache[text_hash]
            self._misses += 1
            return None

    def put(self, text_hash: str, result: EmbeddingResult) -> None:
        """Store an embedding in the cache.

        If the cache is full, evicts the least recently used entry.
        """
        with self._lock:
            if text_hash in self._cache:
                # Update existing entry and move to end
                self._cache.move_to_end(text_hash)
                self._cache[text_hash] = result
            else:
                if len(self._cache) >= self._max_size:
                    # Evict least recently used
                    evicted = self._cache.popitem(last=False)
                    self._evictions += 1
                    logger.debug(
                        "embedding cache evicted: hash=%s...",
                        evicted[0][:12],
                    )
                self._cache[text_hash] = result

    def remove(self, text_hash: str) -> bool:
        """Remove a specific entry from the cache."""
        with self._lock:
            if text_hash in self._cache:
                del self._cache[text_hash]
                return True
            return False

    def clear(self) -> int:
        """Clear the entire cache. Returns the number of entries removed."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    # -- queries -----------------------------------------------------------

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def evictions(self) -> int:
        return self._evictions

    @property
    def hit_rate(self) -> float:
        """Compute cache hit rate as a fraction (0.0 to 1.0)."""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    @property
    def max_size(self) -> int:
        return self._max_size

    def contains(self, text_hash: str) -> bool:
        """Check if a hash is in the cache without affecting LRU order."""
        with self._lock:
            return text_hash in self._cache

    def get_many(
        self, text_hashes: List[str]
    ) -> Tuple[List[EmbeddingResult], List[str]]:
        """Batch get: returns (cached_results, missing_hashes)."""
        cached: List[EmbeddingResult] = []
        missing: List[str] = []
        with self._lock:
            for h in text_hashes:
                if h in self._cache:
                    self._cache.move_to_end(h)
                    self._hits += 1
                    cached.append(self._cache[h])
                else:
                    self._misses += 1
                    missing.append(h)
        return cached, missing

    def put_many(
        self, items: List[Tuple[str, EmbeddingResult]]
    ) -> None:
        """Batch put multiple embeddings."""
        for text_hash, result in items:
            self.put(text_hash, result)

    # -- statistics --------------------------------------------------------

    def stats(self) -> Dict[str, any]:
        """Return cache statistics."""
        return {
            "size": self.size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self._evictions,
            "utilization": round(self.size / self._max_size, 4) if self._max_size > 0 else 0.0,
        }

    def reset_stats(self) -> None:
        """Reset hit/miss/eviction counters without clearing the cache."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0