"""Tests for MemoryMetrics."""

import pytest

from packages.multiagent.src.multiagent.memctx.metrics import (
    MemoryMetrics,
    _LatencyTracker,
)
from packages.multiagent.src.multiagent.memctx.models import MemoryType


class TestLatencyTracker:
    def test_record_and_avg(self):
        lt = _LatencyTracker()
        lt.record(10.0)
        lt.record(20.0)
        assert lt.count == 2
        assert lt.avg == 15.0

    def test_empty_returns_zero(self):
        lt = _LatencyTracker()
        assert lt.count == 0
        assert lt.avg == 0.0
        assert lt.p50 == 0.0
        assert lt.p95 == 0.0
        assert lt.p99 == 0.0
        assert lt.max == 0.0

    def test_percentiles(self):
        lt = _LatencyTracker()
        for i in range(100):
            lt.record(float(i + 1))
        assert lt.p50 == 51.0  # index 50 of 1..100
        assert lt.p95 == 96.0
        assert lt.p99 == 100.0

    def test_max_samples(self):
        lt = _LatencyTracker(max_samples=5)
        for i in range(10):
            lt.record(float(i))
        assert lt.count == 5  # deque maxlen=5
        assert lt.avg == 7.0  # 5+6+7+8+9 / 5

    def test_max_value(self):
        lt = _LatencyTracker()
        lt.record(5.0)
        lt.record(10.0)
        lt.record(3.0)
        assert lt.max == 10.0


class TestMemoryMetricsRecording:
    def test_record_store(self):
        m = MemoryMetrics()
        m.record_store(MemoryType.KNOWLEDGE, 5.0)
        m.record_store(MemoryType.CONVERSATION, 3.0)
        s = m.snapshot()
        assert s["operations"]["store"] == 2
        assert s["by_type"]["knowledge"] == 1
        assert s["by_type"]["conversation"] == 1
        assert s["latency_ms"]["store"]["avg"] == 4.0

    def test_record_update(self):
        m = MemoryMetrics()
        m.record_update(MemoryType.KNOWLEDGE, 2.0)
        s = m.snapshot()
        assert s["operations"]["update"] == 1

    def test_record_delete(self):
        m = MemoryMetrics()
        m.record_delete(1.5)
        s = m.snapshot()
        assert s["operations"]["delete"] == 1

    def test_record_retrieve_hit(self):
        m = MemoryMetrics()
        m.record_retrieve(1.0, hit=True)
        s = m.snapshot()
        assert s["operations"]["retrieve"] == 1
        assert s["hit_rate"]["retrieve_hits"] == 1
        assert s["hit_rate"]["retrieve"] == 1.0

    def test_record_retrieve_miss(self):
        m = MemoryMetrics()
        m.record_retrieve(0.5, hit=False)
        s = m.snapshot()
        assert s["hit_rate"]["retrieve"] == 0.0

    def test_record_search(self):
        m = MemoryMetrics()
        m.record_search(10.0, 5)
        m.record_search(8.0, 3)
        s = m.snapshot()
        assert s["operations"]["search"] == 2
        assert s["search"]["total_results"] == 8
        assert s["search"]["avg_results"] == 4.0

    def test_record_context_build(self):
        m = MemoryMetrics()
        m.record_context_build(tokens=1000, was_trimmed=False)
        m.record_context_build(tokens=500, was_trimmed=True)
        s = m.snapshot()
        assert s["context"]["builds"] == 2
        assert s["context"]["trims"] == 1
        assert s["context"]["avg_tokens"] == 750.0

    def test_record_context_trim(self):
        m = MemoryMetrics()
        m.record_context_build(tokens=100, was_trimmed=True)
        s = m.snapshot()
        assert s["context"]["trims"] == 1


class TestMemoryMetricsSnapshot:
    def test_snapshot_structure(self):
        m = MemoryMetrics()
        m.record_store(MemoryType.KNOWLEDGE, 1.0)
        s = m.snapshot()
        assert "operations" in s
        assert "hit_rate" in s
        assert "latency_ms" in s
        assert "by_type" in s
        assert "context" in s
        assert "search" in s

    def test_latency_structure(self):
        m = MemoryMetrics()
        m.record_store(MemoryType.KNOWLEDGE, 1.0)
        lat = m.snapshot()["latency_ms"]["store"]
        assert "avg" in lat
        assert "p50" in lat
        assert "p95" in lat
        assert "p99" in lat
        assert "samples" in lat

    def test_empty_hit_rate(self):
        m = MemoryMetrics()
        s = m.snapshot()
        assert s["hit_rate"]["retrieve"] == 0.0

    def test_empty_search_avg(self):
        m = MemoryMetrics()
        s = m.snapshot()
        assert s["search"]["avg_results"] == 0

    def test_empty_context_avg(self):
        m = MemoryMetrics()
        s = m.snapshot()
        assert s["context"]["avg_tokens"] == 0


class TestMemoryMetricsReset:
    def test_reset(self):
        m = MemoryMetrics()
        m.record_store(MemoryType.KNOWLEDGE, 1.0)
        m.record_retrieve(0.5, True)
        m.record_context_build(100, False)
        m.reset()
        s = m.snapshot()
        assert s["operations"]["store"] == 0
        assert s["operations"]["retrieve"] == 0
        assert s["context"]["builds"] == 0