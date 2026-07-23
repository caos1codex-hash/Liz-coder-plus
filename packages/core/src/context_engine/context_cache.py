"""Context Cache — in-memory cache with automatic invalidation.

Sprint 3.9.

Caches assembled contexts to avoid redundant file selection,
ranking, budgeting and compression. Supports TTL-based and
event-based invalidation.

Example::

    cache = ContextCache(ttl_seconds=300)
    key = cache.make_key(task="Fix login", agent="coder", session="s1")
    if cache.has(key):
        ctx = cache.get(key)
    else:
        ctx = await builder.build(...)
        cache.put(key, ctx)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .models import AgentContext

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A single cache entry.

    Attributes:
        context:     The cached AgentContext.
        created_at:  Monotonic timestamp when the entry was created.
        hit_count:   Number of cache hits for this entry.
        key:         The cache key.
        size_bytes:  Approximate size in bytes.
    """

    context: AgentContext
    created_at: float = field(default_factory=time.monotonic)
    hit_count: int = 0
    key: str = ""
    size_bytes: int = 0


class ContextCache:
    """In-memory cache for assembled agent contexts.

    Features:
    - TTL-based expiration.
    - Maximum entry count with LRU eviction.
    - Manual invalidation by key pattern.
    - Hit/miss statistics.

    Args:
        ttl_seconds:       Time-to-live for each cache entry.
        max_entries:       Maximum number of entries (0 = unlimited).
        enabled:           Whether caching is enabled.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_entries: int = 100,
        enabled: bool = True,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._enabled = enabled
        self._store: dict[str, CacheEntry] = {}
        self._access_order: list[str] = []  # For LRU eviction.
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def make_key(
        self,
        *,
        task: str,
        agent: str = "",
        session: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Generate a deterministic cache key from inputs.

        The key is a SHA-256 hash of the normalized inputs, which makes
        it safe as a dict key and stable across calls with identical inputs.
        """
        data = {
            "task": task.lower().strip(),
            "agent": agent,
            "session": session,
            **(extra or {}),
        }
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, key: str) -> AgentContext | None:
        """Retrieve a cached context if it exists and is not expired.

        Returns:
            The cached AgentContext, or None on miss.
        """
        if not self._enabled:
            self._misses += 1
            return None

        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            logger.debug("Cache miss: %s", key[:12])
            return None

        # Check TTL.
        age = time.monotonic() - entry.created_at
        if age > self._ttl:
            self._evict(key)
            self._misses += 1
            logger.debug("Cache expired: %s (age=%.1fs)", key[:12], age)
            return None

        # Hit — update LRU and stats.
        entry.hit_count += 1
        self._hits += 1
        self._touch_lru(key)

        logger.debug(
            "Cache hit: %s (hits=%d, age=%.1fs)",
            key[:12],
            entry.hit_count,
            age,
        )
        return entry.context

    def put(self, key: str, context: AgentContext) -> None:
        """Store a context in the cache.

        If the cache is full, the least-recently-used entry is evicted.
        """
        if not self._enabled:
            return

        # Evict if at capacity.
        if self._max_entries > 0 and len(self._store) >= self._max_entries:
            if key not in self._store:
                self._evict_lru()

        # Estimate size.
        size = len(context.task) + sum(len(f.content) for f in context.files)
        size += sum(len(str(m.value)) for m in context.memories)
        size += sum(len(h.content) for h in context.history)

        entry = CacheEntry(
            context=context,
            key=key,
            size_bytes=size,
        )
        self._store[key] = entry
        self._touch_lru(key)

        logger.debug(
            "Cache put: %s (size=%d, total=%d)",
            key[:12],
            size,
            len(self._store),
        )

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        if not self._enabled:
            return False
        entry = self._store.get(key)
        if entry is None:
            return False
        if time.monotonic() - entry.created_at > self._ttl:
            self._evict(key)
            return False
        return True

    def invalidate(self, key: str) -> bool:
        """Remove a specific entry from the cache.

        Returns:
            True if the entry was found and removed.
        """
        if key in self._store:
            self._evict(key)
            logger.debug("Cache invalidated: %s", key[:12])
            return True
        return False

    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all entries whose key contains the pattern.

        Args:
            pattern: Substring to match against cache keys.

        Returns:
            Number of entries invalidated.
        """
        keys_to_remove = [k for k in self._store if pattern in k]
        for k in keys_to_remove:
            self._evict(k)
        logger.debug(
            "Cache invalidated %d entries matching '%s'",
            len(keys_to_remove),
            pattern,
        )
        return len(keys_to_remove)

    def invalidate_all(self) -> int:
        """Clear the entire cache.

        Returns:
            Number of entries cleared.
        """
        count = len(self._store)
        self._store.clear()
        self._access_order.clear()
        logger.debug("Cache cleared (%d entries)", count)
        return count

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "enabled": self._enabled,
            "size": len(self._store),
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 4),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict(self, key: str) -> None:
        """Remove a single entry."""
        self._store.pop(key, None)
        if key in self._access_order:
            self._access_order.remove(key)

    def _touch_lru(self, key: str) -> None:
        """Move a key to the end of the access order (most recent)."""
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def _evict_lru(self) -> None:
        """Evict the least-recently-used entry."""
        if self._access_order:
            lru_key = self._access_order.pop(0)
            self._store.pop(lru_key, None)
            logger.debug("Cache LRU evicted: %s", lru_key[:12])


__all__ = [
    "CacheEntry",
    "ContextCache",
]
