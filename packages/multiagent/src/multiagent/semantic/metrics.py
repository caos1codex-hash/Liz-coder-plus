"""Semantic memory metrics.

Tracks operational metrics for the semantic layer:
- Embedding generation count and latency
- Semantic and hybrid search count and latency
- Cache hit/miss rates
- Index size and usage
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional


class _SimpleLatencyTracker:
    """Lightweight latency tracker."""

    def __init__(self, max_samples: int = 500) -> None:
        self._samples: Deque[float] = deque(maxlen=max_samples)

    def record(self, latency_ms: float) -> None:
        self._samples.append(latency_ms)

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def avg(self) -> float:
        if not self._samples:
            return 0.0
        return statistics.mean(self._samples)

    @property
    def p50(self) -> float:
        if not self._samples:
            return 0.0
        s = sorted(self._samples)
        return s[len(s) // 2]

    @property
    def p95(self) -> float:
        if not self._samples:
            return 0.0
        s = sorted(self._samples)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]


class SemanticMetrics:
    """Collects and reports metrics for the semantic memory system."""

    def __init__(self, max_samples: int = 500) -> None:
        self._embedding_count = 0
        self._semantic_search_count = 0
        self._hybrid_search_count = 0
        self._text_search_count = 0
        self._cache_hits = 0
        self._cache_misses = 0

        self._embedding_latency = _SimpleLatencyTracker(max_samples)
        self._semantic_search_latency = _SimpleLatencyTracker(max_samples)
        self._hybrid_search_latency = _SimpleLatencyTracker(max_samples)
        self._text_search_latency = _SimpleLatencyTracker(max_samples)

        self._index_size_history: Deque[int] = deque(maxlen=100)
        self._memory_usage_samples: Deque[float] = deque(maxlen=100)

    # -- recording methods -------------------------------------------------

    def record_embedding_generated(self, latency_ms: float) -> None:
        self._embedding_count += 1
        self._embedding_latency.record(latency_ms)

    def record_semantic_search(self, latency_ms: float, result_count: int) -> None:
        self._semantic_search_count += 1
        self._semantic_search_latency.record(latency_ms)

    def record_hybrid_search(self, latency_ms: float, result_count: int) -> None:
        self._hybrid_search_count += 1
        self._hybrid_search_latency.record(latency_ms)

    def record_text_search(self, latency_ms: float, result_count: int) -> None:
        self._text_search_count += 1
        self._text_search_latency.record(latency_ms)

    def record_cache_hit(self) -> None:
        self._cache_hits += 1

    def record_cache_miss(self) -> None:
        self._cache_misses += 1

    def record_index_size(self, size: int) -> None:
        self._index_size_history.append(size)

    def record_memory_usage_mb(self, usage_mb: float) -> None:
        self._memory_usage_samples.append(usage_mb)

    # -- snapshot ----------------------------------------------------------

    @property
    def cache_hit_rate(self) -> float:
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0.0
        return self._cache_hits / total

    def snapshot(self) -> Dict[str, Any]:
        """Return a comprehensive snapshot of all semantic metrics."""
        return {
            "embeddings": {
                "total_generated": self._embedding_count,
                "avg_latency_ms": round(self._embedding_latency.avg, 2),
                "p50_latency_ms": round(self._embedding_latency.p50, 2),
                "p95_latency_ms": round(self._embedding_latency.p95, 2),
            },
            "searches": {
                "semantic": {
                    "count": self._semantic_search_count,
                    "avg_latency_ms": round(self._semantic_search_latency.avg, 2),
                    "p50_latency_ms": round(self._semantic_search_latency.p50, 2),
                },
                "hybrid": {
                    "count": self._hybrid_search_count,
                    "avg_latency_ms": round(self._hybrid_search_latency.avg, 2),
                    "p50_latency_ms": round(self._hybrid_search_latency.p50, 2),
                },
                "text": {
                    "count": self._text_search_count,
                    "avg_latency_ms": round(self._text_search_latency.avg, 2),
                    "p50_latency_ms": round(self._text_search_latency.p50, 2),
                },
            },
            "cache": {
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": round(self.cache_hit_rate, 4),
            },
            "index": {
                "current_size": self._index_size_history[-1] if self._index_size_history else 0,
                "size_history": list(self._index_size_history),
            },
            "memory": {
                "avg_usage_mb": (
                    round(statistics.mean(self._memory_usage_samples), 2)
                    if self._memory_usage_samples
                    else 0.0
                ),
            },
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._embedding_count = 0
        self._semantic_search_count = 0
        self._hybrid_search_count = 0
        self._text_search_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._index_size_history.clear()
        self._memory_usage_samples.clear()