"""Memory system metrics.

Tracks operational metrics for the memory engine:
- Store/update/delete/retrieve counts and latencies
- Search performance
- Context build statistics
- Storage utilization
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from .models import MemoryType


@dataclass
class _LatencyTracker:
    """Tracks latency samples for an operation."""

    max_samples: int = 1000
    _samples: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))

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

    @property
    def p99(self) -> float:
        if not self._samples:
            return 0.0
        s = sorted(self._samples)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)]

    @property
    def max(self) -> float:
        if not self._samples:
            return 0.0
        return max(self._samples)


class MemoryMetrics:
    """Collects and reports metrics for the memory system."""

    def __init__(self, max_samples: int = 1000) -> None:
        self._store_count = 0
        self._update_count = 0
        self._delete_count = 0
        self._retrieve_count = 0
        self._retrieve_hits = 0
        self._search_count = 0
        self._search_result_count = 0

        self._store_latency = _LatencyTracker(max_samples)
        self._update_latency = _LatencyTracker(max_samples)
        self._delete_latency = _LatencyTracker(max_samples)
        self._retrieve_latency = _LatencyTracker(max_samples)
        self._search_latency = _LatencyTracker(max_samples)

        self._by_type: Dict[str, int] = defaultdict(int)
        self._context_builds = 0
        self._context_trims = 0
        self._context_tokens_avg: Deque[int] = deque(maxlen=max_samples)

    # -- recording methods -------------------------------------------------

    def record_store(self, memory_type: MemoryType, latency_ms: float) -> None:
        self._store_count += 1
        self._store_latency.record(latency_ms)
        self._by_type[memory_type.value] += 1

    def record_update(self, memory_type: MemoryType, latency_ms: float) -> None:
        self._update_count += 1
        self._update_latency.record(latency_ms)

    def record_delete(self, latency_ms: float) -> None:
        self._delete_count += 1
        self._delete_latency.record(latency_ms)

    def record_retrieve(self, latency_ms: float, hit: bool) -> None:
        self._retrieve_count += 1
        self._retrieve_latency.record(latency_ms)
        if hit:
            self._retrieve_hits += 1

    def record_search(self, latency_ms: float, result_count: int) -> None:
        self._search_count += 1
        self._search_latency.record(latency_ms)
        self._search_result_count += result_count

    def record_context_build(self, tokens: int, was_trimmed: bool) -> None:
        self._context_builds += 1
        self._context_tokens_avg.append(tokens)
        if was_trimmed:
            self._context_trims += 1

    # -- snapshot ----------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a snapshot of all metrics."""
        return {
            "operations": {
                "store": self._store_count,
                "update": self._update_count,
                "delete": self._delete_count,
                "retrieve": self._retrieve_count,
                "search": self._search_count,
            },
            "hit_rate": {
                "retrieve": (
                    self._retrieve_hits / self._retrieve_count
                    if self._retrieve_count > 0
                    else 0.0
                ),
                "retrieve_hits": self._retrieve_hits,
            },
            "latency_ms": {
                "store": {
                    "avg": round(self._store_latency.avg, 2),
                    "p50": round(self._store_latency.p50, 2),
                    "p95": round(self._store_latency.p95, 2),
                    "p99": round(self._store_latency.p99, 2),
                    "samples": self._store_latency.count,
                },
                "retrieve": {
                    "avg": round(self._retrieve_latency.avg, 2),
                    "p50": round(self._retrieve_latency.p50, 2),
                    "p95": round(self._retrieve_latency.p95, 2),
                    "p99": round(self._retrieve_latency.p99, 2),
                    "samples": self._retrieve_latency.count,
                },
                "search": {
                    "avg": round(self._search_latency.avg, 2),
                    "p50": round(self._search_latency.p50, 2),
                    "p95": round(self._search_latency.p95, 2),
                    "p99": round(self._search_latency.p99, 2),
                    "samples": self._search_latency.count,
                },
            },
            "by_type": dict(self._by_type),
            "context": {
                "builds": self._context_builds,
                "trims": self._context_trims,
                "avg_tokens": (
                    round(statistics.mean(self._context_tokens_avg), 1)
                    if self._context_tokens_avg
                    else 0
                ),
            },
            "search": {
                "total_results": self._search_result_count,
                "avg_results": (
                    round(
                        self._search_result_count / self._search_count, 1
                    )
                    if self._search_count > 0
                    else 0
                ),
            },
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._store_count = 0
        self._update_count = 0
        self._delete_count = 0
        self._retrieve_count = 0
        self._retrieve_hits = 0
        self._search_count = 0
        self._search_result_count = 0
        self._by_type.clear()
        self._context_builds = 0
        self._context_trims = 0
        self._context_tokens_avg.clear()