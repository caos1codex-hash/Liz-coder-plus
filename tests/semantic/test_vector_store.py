"""Tests for VectorStore API and InMemoryVectorStore."""

import math
import pytest

from multiagent.semantic.vector_store import (
    InMemoryVectorStore,
    VectorSearchResult,
    VectorStore,
    cosine_similarity,
    dot_product,
    euclidean_distance,
)


# ---------------------------------------------------------------------------
# Similarity function tests
# ---------------------------------------------------------------------------


class TestSimilarityFunctions:
    def test_cosine_identical(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_cosine_orthogonal(self):
        assert abs(cosine_similarity([1, 0], [0, 1])) < 1e-9

    def test_cosine_opposite(self):
        result = cosine_similarity([1.0, 0.0], [-1.0, 0.0])
        assert abs(result - (-1.0)) < 1e-9

    def test_cosine_dimension_mismatch(self):
        with pytest.raises(ValueError):
            cosine_similarity([1, 2], [1, 2, 3])

    def test_cosine_zero_vectors(self):
        result = cosine_similarity([0, 0], [1, 1])
        assert result == 0.0

    def test_euclidean_identical(self):
        assert euclidean_distance([1, 2], [1, 2]) == 0.0

    def test_euclidean_distance(self):
        d = euclidean_distance([0, 0], [3, 4])
        assert abs(d - 5.0) < 1e-9

    def test_euclidean_dimension_mismatch(self):
        with pytest.raises(ValueError):
            euclidean_distance([1], [1, 2])

    def test_dot_product(self):
        assert dot_product([1, 2, 3], [4, 5, 6]) == 32.0

    def test_dot_product_dimension_mismatch(self):
        with pytest.raises(ValueError):
            dot_product([1], [1, 2])


# ---------------------------------------------------------------------------
# InMemoryVectorStore tests
# ---------------------------------------------------------------------------


class TestInMemoryVectorStore:
    @pytest.fixture
    def store(self):
        return InMemoryVectorStore()

    @pytest.fixture
    def store_with_dims(self):
        return InMemoryVectorStore(expected_dims=4)

    def _unit_vec(self, *values):
        """Create a unit vector from values."""
        mag = math.sqrt(sum(x * x for x in values))
        if mag == 0:
            return list(values)
        return [x / mag for x in values]

    @pytest.mark.asyncio
    async def test_add_and_get(self, store):
        vec = [0.1, 0.2, 0.3, 0.4]
        await store.add("v1", vec, {"tag": "test"})
        result = await store.get("v1")
        assert result is not None
        retrieved_vec, meta = result
        assert retrieved_vec == vec
        assert meta == {"tag": "test"}

    @pytest.mark.asyncio
    async def test_add_duplicate_raises(self, store):
        await store.add("v1", [1.0, 0.0])
        with pytest.raises(ValueError, match="duplicate"):
            await store.add("v1", [0.0, 1.0])

    @pytest.mark.asyncio
    async def test_add_validates_dimensions(self, store_with_dims):
        with pytest.raises(ValueError, match="expected 4 dimensions"):
            await store_with_dims.add("v1", [1.0, 2.0, 3.0])

    @pytest.mark.asyncio
    async def test_add_correct_dimensions(self, store_with_dims):
        await store_with_dims.add("v1", [1.0, 2.0, 3.0, 4.0])
        assert await store_with_dims.exists("v1")

    @pytest.mark.asyncio
    async def test_update(self, store):
        await store.add("v1", [1.0, 0.0])
        updated = await store.update("v1", [0.0, 1.0], {"new": True})
        assert updated is True
        vec, meta = await store.get("v1")
        assert vec == [0.0, 1.0]
        assert meta == {"new": True}

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, store):
        assert await store.update("v1", [1.0, 0.0]) is False

    @pytest.mark.asyncio
    async def test_update_validates_dimensions(self, store_with_dims):
        await store_with_dims.add("v1", [1.0, 2.0, 3.0, 4.0])
        with pytest.raises(ValueError):
            await store_with_dims.update("v1", [1.0, 2.0, 3.0])

    @pytest.mark.asyncio
    async def test_remove(self, store):
        await store.add("v1", [1.0, 0.0])
        assert await store.remove("v1") is True
        assert await store.exists("v1") is False

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, store):
        assert await store.remove("v1") is False

    @pytest.mark.asyncio
    async def test_search_basic(self, store):
        v1 = self._unit_vec(1, 0, 0)
        v2 = self._unit_vec(0, 1, 0)
        v3 = self._unit_vec(0.5, 0.5, 0)
        query = self._unit_vec(1, 0, 0)

        await store.add("v1", v1)
        await store.add("v2", v2)
        await store.add("v3", v3)

        results = await store.search(query, top_k=2)
        assert len(results) <= 2
        # v1 should be the top result (identical to query)
        assert results[0].id == "v1"
        assert abs(results[0].score - 1.0) < 1e-9

    @pytest.mark.asyncio
    async def test_search_with_min_score(self, store):
        v1 = self._unit_vec(1, 0)
        v2 = self._unit_vec(0, 1)
        query = self._unit_vec(1, 0)

        await store.add("v1", v1)
        await store.add("v2", v2)

        results = await store.search(query, min_score=0.9)
        assert len(results) == 1
        assert results[0].id == "v1"

    @pytest.mark.asyncio
    async def test_search_with_metadata_filters(self, store):
        v1 = self._unit_vec(1, 0)
        v2 = self._unit_vec(1, 0)
        query = self._unit_vec(1, 0)

        await store.add("v1", v1, {"type": "knowledge"})
        await store.add("v2", v2, {"type": "conversation"})

        results = await store.search(query, filters={"type": "knowledge"})
        assert len(results) == 1
        assert results[0].id == "v1"

    @pytest.mark.asyncio
    async def test_search_empty_store(self, store):
        results = await store.search([1.0, 0.0])
        assert results == []

    @pytest.mark.asyncio
    async def test_search_sorted_by_score(self, store):
        for i in range(10):
            v = self._unit_vec(1.0 / (i + 1), 1.0)
            await store.add(f"v{i}", v)
        query = self._unit_vec(1, 0)
        results = await store.search(query, top_k=5)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_exists(self, store):
        assert await store.exists("v1") is False
        await store.add("v1", [1.0, 0.0])
        assert await store.exists("v1") is True

    @pytest.mark.asyncio
    async def test_clear(self, store):
        await store.add("v1", [1.0])
        await store.add("v2", [2.0])
        count = await store.clear()
        assert count == 2
        assert await store.size() == 0

    @pytest.mark.asyncio
    async def test_size(self, store):
        assert await store.size() == 0
        await store.add("v1", [1.0])
        assert await store.size() == 1
        await store.add("v2", [2.0])
        assert await store.size() == 2

    @pytest.mark.asyncio
    async def test_add_batch(self, store):
        ids = ["v1", "v2", "v3"]
        vectors = [[1.0], [2.0], [3.0]]
        count = await store.add_batch(ids, vectors)
        assert count == 3
        assert await store.size() == 3

    @pytest.mark.asyncio
    async def test_add_batch_mismatched_lengths(self, store):
        with pytest.raises(ValueError):
            await store.add_batch(["v1"], [[1.0], [2.0]])

    @pytest.mark.asyncio
    async def test_add_batch_with_metadatas(self, store):
        count = await store.add_batch(
            ["v1", "v2"],
            [[1.0], [2.0]],
            [{"a": 1}, {"b": 2}],
        )
        assert count == 2
        _, meta = await store.get("v1")
        assert meta == {"a": 1}

    @pytest.mark.asyncio
    async def test_add_batch_skips_duplicates(self, store):
        await store.add("v1", [1.0])
        count = await store.add_batch(["v1", "v2"], [[1.0], [2.0]])
        assert count == 1

    def test_stats(self, store):
        s = store.stats()
        assert s["type"] == "in_memory"
        assert s["vectors_stored"] == 0

    @pytest.mark.asyncio
    async def test_search_result_to_dict(self):
        result = VectorSearchResult(
            id="test", vector=[1.0, 2.0], score=0.95, metadata={"k": "v"}
        )
        d = result.to_dict()
        assert d["id"] == "test"
        assert d["score"] == 0.95
        assert d["metadata"] == {"k": "v"}
        assert d["dimensions"] == 2


# ---------------------------------------------------------------------------
# ABC tests
# ---------------------------------------------------------------------------


class TestVectorStoreABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            VectorStore()