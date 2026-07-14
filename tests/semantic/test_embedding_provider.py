"""Tests for the EmbeddingProvider API and DummyEmbeddingProvider."""

import pytest

from multiagent.semantic.embedding import (
    DummyEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingResult,
)


# ---------------------------------------------------------------------------
# EmbeddingResult tests
# ---------------------------------------------------------------------------


class TestEmbeddingResult:
    def test_basic_creation(self):
        result = EmbeddingResult(vector=[0.1, 0.2, 0.3], provider="test")
        assert result.vector == [0.1, 0.2, 0.3]
        assert result.provider == "test"
        assert result.dimensions == 3
        assert result.text_hash == ""
        assert result.latency_ms == 0.0

    def test_auto_dimensions(self):
        result = EmbeddingResult(vector=[1.0] * 64)
        assert result.dimensions == 64

    def test_default_provider(self):
        result = EmbeddingResult(vector=[0.5])
        assert result.provider == "unknown"

    def test_magnitude(self):
        result = EmbeddingResult(vector=[3.0, 4.0])
        assert abs(result.magnitude - 5.0) < 1e-9

    def test_magnitude_zero(self):
        result = EmbeddingResult(vector=[0.0, 0.0])
        assert result.magnitude == 0.0

    def test_normalized(self):
        result = EmbeddingResult(vector=[3.0, 4.0])
        norm = result.normalized()
        mag = sum(x * x for x in norm) ** 0.5
        assert abs(mag - 1.0) < 1e-9

    def test_normalized_zero_vector(self):
        result = EmbeddingResult(vector=[0.0, 0.0, 0.0])
        norm = result.normalized()
        assert norm == [0.0, 0.0, 0.0]

    def test_to_dict(self):
        result = EmbeddingResult(
            vector=[0.1, 0.2],
            text_hash="abc123",
            provider="test",
            dimensions=2,
            latency_ms=5.0,
            metadata={"model": "test-model"},
        )
        d = result.to_dict()
        assert d["vector"] == [0.1, 0.2]
        assert d["text_hash"] == "abc123"
        assert d["provider"] == "test"
        assert d["dimensions"] == 2
        assert d["latency_ms"] == 5.0
        assert d["metadata"] == {"model": "test-model"}


# ---------------------------------------------------------------------------
# DummyEmbeddingProvider tests
# ---------------------------------------------------------------------------


class TestDummyEmbeddingProvider:
    @pytest.fixture
    def provider(self):
        return DummyEmbeddingProvider(dims=32, seed=42)

    @pytest.mark.asyncio
    async def test_embed_text_returns_result(self, provider):
        result = await provider.embed_text("hello world")
        assert isinstance(result, EmbeddingResult)
        assert len(result.vector) == 32
        assert result.provider == "dummy"
        assert result.dimensions == 32

    @pytest.mark.asyncio
    async def test_embed_text_deterministic(self, provider):
        r1 = await provider.embed_text("same text")
        r2 = await provider.embed_text("same text")
        assert r1.vector == r2.vector
        assert r1.text_hash == r2.text_hash

    @pytest.mark.asyncio
    async def test_embed_text_different_inputs(self, provider):
        r1 = await provider.embed_text("text A")
        r2 = await provider.embed_text("text B")
        # Different texts should produce different vectors (with very high probability)
        assert r1.vector != r2.vector

    @pytest.mark.asyncio
    async def test_embed_text_normalized(self, provider):
        result = await provider.embed_text("normalization test")
        mag = result.magnitude
        assert abs(mag - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_embed_text_hash_computed(self, provider):
        result = await provider.embed_text("hash test")
        expected_hash = provider.compute_text_hash("hash test")
        assert result.text_hash == expected_hash

    @pytest.mark.asyncio
    async def test_embed_batch(self, provider):
        texts = ["first", "second", "third"]
        results = await provider.embed_batch(texts)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, EmbeddingResult)
            assert len(r.vector) == 32

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self, provider):
        results = await provider.embed_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_batch_matches_single(self, provider):
        texts = ["alpha", "beta", "gamma"]
        batch_results = await provider.embed_batch(texts)
        single_results = [await provider.embed_text(t) for t in texts]
        for br, sr in zip(batch_results, single_results):
            assert br.vector == sr.vector

    def test_dimensions(self, provider):
        assert provider.dimensions() == 32

    def test_dimensions_custom(self):
        p = DummyEmbeddingProvider(dims=256)
        assert p.dimensions() == 256

    def test_provider_name(self, provider):
        assert provider.provider_name() == "dummy"

    @pytest.mark.asyncio
    async def test_health(self, provider):
        health = await provider.health()
        assert health["status"] == "healthy"
        assert health["provider"] == "dummy"
        assert health["dimensions"] == 32
        assert "calls" in health

    def test_call_count(self, provider):
        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_call_count_increments(self, provider):
        await provider.embed_text("test")
        assert provider.call_count == 1
        await provider.embed_text("test2")
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_text_whitespace_ignored_in_hash(self, provider):
        r1 = await provider.embed_text("hello")
        r2 = await provider.embed_text("  hello  ")
        assert r1.text_hash == r2.text_hash
        assert r1.vector == r2.vector

    @pytest.mark.asyncio
    async def test_large_dimensions(self):
        p = DummyEmbeddingProvider(dims=1024)
        result = await p.embed_text("large dims test")
        assert len(result.vector) == 1024
        assert result.dimensions == 1024


# ---------------------------------------------------------------------------
# Abstract class tests
# ---------------------------------------------------------------------------


class TestEmbeddingProviderABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            EmbeddingProvider()

    def test_compute_text_hash(self):
        p = DummyEmbeddingProvider()
        h1 = p.compute_text_hash("test")
        h2 = p.compute_text_hash("test")
        h3 = p.compute_text_hash("other")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 64  # SHA-256 hex