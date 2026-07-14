"""Tests for SemanticIndex."""

import pytest

from multiagent.memctx.engine import MemoryEngine
from multiagent.memctx.models import KnowledgeMemory, MemoryRecord, MemoryType
from multiagent.semantic.cache import EmbeddingCache
from multiagent.semantic.embedding import DummyEmbeddingProvider
from multiagent.semantic.semantic_index import SemanticIndex
from multiagent.semantic.vector_store import InMemoryVectorStore


@pytest.fixture
async def engine():
    e = MemoryEngine()
    await e.start()
    yield e
    await e.stop()


@pytest.fixture
def provider():
    return DummyEmbeddingProvider(dims=32, seed=42)


@pytest.fixture
def vector_store():
    return InMemoryVectorStore()


@pytest.fixture
def cache():
    return EmbeddingCache()


@pytest.fixture
async def sem_index(provider, vector_store, cache, engine):
    idx = SemanticIndex(
        embedding_provider=provider,
        vector_store=vector_store,
        cache=cache,
        metrics=None,
    )
    await idx.start(engine)
    yield idx
    await idx.stop()


class TestSemanticIndex:
    @pytest.mark.asyncio
    async def test_start_indexes_existing_records(self, engine, provider, vector_store):
        # Add records before starting index
        for i in range(5):
            rec = MemoryRecord(
                id=f"mem-{i}",
                content={"text": f"test content about topic {i}"},
                memory_type=MemoryType.KNOWLEDGE,
            )
            await engine.store(rec)

        idx = SemanticIndex(provider, vector_store)
        await idx.start(engine)
        assert idx.size == 5
        await idx.stop()

    @pytest.mark.asyncio
    async def test_index_record(self, sem_index, engine):
        rec = MemoryRecord(
            id="test-1",
            content={"text": "semantic search is about meaning"},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await engine.store(rec)
        await sem_index.index_record(rec)
        assert sem_index.size == 1

    @pytest.mark.asyncio
    async def test_index_record_empty_text(self, sem_index):
        rec = MemoryRecord(
            id="empty-1",
            content={"text": ""},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await sem_index.index_record(rec)
        # Should skip empty text
        assert sem_index.size == 0

    @pytest.mark.asyncio
    async def test_update_record(self, sem_index, engine):
        rec = MemoryRecord(
            id="upd-1",
            content={"text": "original content"},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await engine.store(rec)
        await sem_index.index_record(rec)

        # Update content
        rec.content = {"text": "updated content about something new"}
        rec.bump_version()
        await engine.update(rec)
        await sem_index.update_record(rec)
        assert sem_index.size == 1

    @pytest.mark.asyncio
    async def test_remove_record(self, sem_index, engine):
        rec = MemoryRecord(
            id="rem-1",
            content={"text": "to be removed"},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await engine.store(rec)
        await sem_index.index_record(rec)
        assert sem_index.size == 1

        removed = await sem_index.remove_record("rem-1")
        assert removed is True
        assert sem_index.size == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, sem_index):
        removed = await sem_index.remove_record("nonexistent")
        assert removed is False

    @pytest.mark.asyncio
    async def test_search_basic(self, sem_index, engine):
        # Index records with different content
        for i, text in enumerate([
            "Python programming language fundamentals",
            "Machine learning with neural networks",
            "Web development with HTML and CSS",
            "Data structures and algorithms",
            "Python web frameworks like Django and Flask",
        ]):
            rec = MemoryRecord(
                id=f"doc-{i}",
                content={"text": text},
                memory_type=MemoryType.KNOWLEDGE,
            )
            await engine.store(rec)
            await sem_index.index_record(rec)

        # Search for Python-related content
        results = await sem_index.search("Python programming", top_k=3)
        assert len(results) > 0
        # Results should be sorted by score
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_search_with_min_score(self, sem_index, engine):
        rec = MemoryRecord(
            id="doc-1",
            content={"text": "completely unrelated topic"},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await engine.store(rec)
        await sem_index.index_record(rec)

        results = await sem_index.search("very specific unique query", min_score=0.99)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_uses_cache(self, sem_index, engine, provider):
        rec = MemoryRecord(
            id="cache-test",
            content={"text": "cacheable content"},
            memory_type=MemoryType.KNOWLEDGE,
        )
        await engine.store(rec)
        await sem_index.index_record(rec)

        # First search - cache miss for query embedding
        await sem_index.search("cache test query")
        # Second search - should hit cache for same query
        await sem_index.search("cache test query")

        # Cache should have entries
        assert sem_index._cache.size > 0

    @pytest.mark.asyncio
    async def test_search_empty_index(self, sem_index):
        results = await sem_index.search("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_use_count_tracking(self, sem_index, engine):
        for i in range(3):
            rec = MemoryRecord(
                id=f"uc-{i}",
                content={"text": f"content {i}"},
                memory_type=MemoryType.KNOWLEDGE,
            )
            await engine.store(rec)
            await sem_index.index_record(rec)

        await sem_index.search("content 0")
        assert sem_index.get_use_count("uc-0") >= 1

    @pytest.mark.asyncio
    async def test_provider_property(self, sem_index, provider):
        assert sem_index.provider is provider

    @pytest.mark.asyncio
    async def test_metrics_property(self, sem_index):
        m = sem_index.metrics
        snapshot = m.snapshot()
        assert "embeddings" in snapshot

    @pytest.mark.asyncio
    async def test_no_vector_store(self, provider, engine):
        """Search should return empty when no vector store is configured."""
        idx = SemanticIndex(provider, vector_store=None)
        await idx.start(engine)
        results = await idx.search("any query")
        assert results == []
        await idx.stop()

    @pytest.mark.asyncio
    async def test_extract_text_from_various_content_types(self, sem_index, engine):
        # String content
        rec_str = MemoryRecord(id="s1", content="string content", memory_type=MemoryType.KNOWLEDGE)
        await engine.store(rec_str)
        await sem_index.index_record(rec_str)

        # Dict content
        rec_dict = MemoryRecord(id="d1", content={"text": "dict content"}, memory_type=MemoryType.KNOWLEDGE)
        await engine.store(rec_dict)
        await sem_index.index_record(rec_dict)

        assert sem_index.size == 2