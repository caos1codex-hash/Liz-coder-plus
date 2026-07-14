"""Tests for SemanticMetrics."""

import pytest

from multiagent.semantic.metrics import SemanticMetrics


class TestSemanticMetrics:
    @pytest.fixture
    def metrics(self):
        return SemanticMetrics()

    def test_initial_snapshot(self, metrics):
        snap = metrics.snapshot()
        assert snap["embeddings"]["total_generated"] == 0
        assert snap["searches"]["semantic"]["count"] == 0
        assert snap["searches"]["hybrid"]["count"] == 0
        assert snap["cache"]["hits"] == 0
        assert snap["cache"]["misses"] == 0
        assert snap["cache"]["hit_rate"] == 0.0

    def test_record_embedding(self, metrics):
        metrics.record_embedding_generated(5.0)
        metrics.record_embedding_generated(10.0)
        snap = metrics.snapshot()
        assert snap["embeddings"]["total_generated"] == 2
        assert snap["embeddings"]["avg_latency_ms"] == 7.5

    def test_record_semantic_search(self, metrics):
        metrics.record_semantic_search(3.0, 5)
        metrics.record_semantic_search(7.0, 10)
        snap = metrics.snapshot()
        assert snap["searches"]["semantic"]["count"] == 2
        assert snap["searches"]["semantic"]["avg_latency_ms"] == 5.0

    def test_record_hybrid_search(self, metrics):
        metrics.record_hybrid_search(15.0, 8)
        snap = metrics.snapshot()
        assert snap["searches"]["hybrid"]["count"] == 1
        assert snap["searches"]["hybrid"]["avg_latency_ms"] == 15.0

    def test_record_text_search(self, metrics):
        metrics.record_text_search(2.0, 3)
        snap = metrics.snapshot()
        assert snap["searches"]["text"]["count"] == 1

    def test_cache_hit_rate(self, metrics):
        metrics.record_cache_hit()
        metrics.record_cache_hit()
        metrics.record_cache_miss()
        assert abs(metrics.cache_hit_rate - (2 / 3)) < 1e-9

    def test_cache_hit_rate_empty(self, metrics):
        assert metrics.cache_hit_rate == 0.0

    def test_record_index_size(self, metrics):
        metrics.record_index_size(42)
        snap = metrics.snapshot()
        assert snap["index"]["current_size"] == 42

    def test_record_memory_usage(self, metrics):
        metrics.record_memory_usage_mb(128.5)
        snap = metrics.snapshot()
        assert snap["memory"]["avg_usage_mb"] == 128.5

    def test_index_size_history(self, metrics):
        for i in range(5):
            metrics.record_index_size(i * 10)
        snap = metrics.snapshot()
        assert len(snap["index"]["size_history"]) == 5
        assert snap["index"]["size_history"] == [0, 10, 20, 30, 40]

    def test_reset(self, metrics):
        metrics.record_embedding_generated(5.0)
        metrics.record_semantic_search(3.0, 5)
        metrics.record_cache_hit()
        metrics.record_cache_miss()
        metrics.record_index_size(10)

        metrics.reset()

        snap = metrics.snapshot()
        assert snap["embeddings"]["total_generated"] == 0
        assert snap["searches"]["semantic"]["count"] == 0
        assert snap["cache"]["hits"] == 0
        assert snap["cache"]["misses"] == 0
        assert snap["index"]["current_size"] == 0

    def test_latency_percentiles(self, metrics):
        for i in range(100):
            metrics.record_embedding_generated(float(i))

        snap = metrics.snapshot()
        assert snap["embeddings"]["p50_latency_ms"] >= 0
        assert snap["embeddings"]["p95_latency_ms"] >= snap["embeddings"]["p50_latency_ms"]

    def test_multiple_memory_usage_averages(self, metrics):
        metrics.record_memory_usage_mb(100.0)
        metrics.record_memory_usage_mb(200.0)
        snap = metrics.snapshot()
        assert snap["memory"]["avg_usage_mb"] == 150.0

    def test_snapshot_completeness(self, metrics):
        metrics.record_embedding_generated(1.0)
        metrics.record_semantic_search(2.0, 3)
        metrics.record_hybrid_search(3.0, 4)
        metrics.record_text_search(4.0, 5)
        metrics.record_cache_hit()
        metrics.record_cache_miss()
        metrics.record_index_size(10)
        metrics.record_memory_usage_mb(50.0)

        snap = metrics.snapshot()
        # Verify all top-level keys
        assert "embeddings" in snap
        assert "searches" in snap
        assert "cache" in snap
        assert "index" in snap
        assert "memory" in snap

        # Verify search sub-keys
        assert "semantic" in snap["searches"]
        assert "hybrid" in snap["searches"]
        assert "text" in snap["searches"]