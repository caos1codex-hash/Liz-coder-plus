"""Tests for SemanticRetrievalEngine."""

import pytest

from multiagent.memctx.engine import MemoryEngine
from multiagent.memctx.models import KnowledgeMemory, MemoryRecord, MemoryType
from multiagent.semantic.cache import EmbeddingCache
from multiagent.semantic.embedding import DummyEmbeddingProvider
from multiagent.semantic.retrieval import (
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    SemanticRetrievalEngine,
)
from multiagent.semantic.ranking import RankingConfig, RankingEngine
from multiagent.semantic.semantic_index import SemanticIndex
from multiagent.semantic.vector_store import InMemoryVectorStore


@pytest.fixture
async def setup():
    """Create a fully wired retrieval engine with indexed data."""
    engine = MemoryEngine()
    await engine.start()

    provider = DummyEmbeddingProvider(dims=32, seed=42)
    store = InMemoryVectorStore()
    cache = EmbeddingCache()
    metrics = None
    event_bus = engine.event_bus

    sem_index = SemanticIndex(provider, store, cache, event_bus, metrics)
    ranking = RankingEngine()

    retrieval = SemanticRetrievalEngine(
        memory_engine=engine,
        embedding_provider=provider,
        semantic_index=sem_index,
        ranking_engine=ranking,
        event_bus=event_bus,
        metrics=metrics,
        cache=cache,
    )

    # Store and index test data
    texts = [
        "Python programming language and its ecosystem",
        "Machine learning with deep neural networks",
        "Web development using HTML CSS and JavaScript",
        "Database design and SQL query optimization",
        "Python web frameworks: Django and Flask",
        "Cloud computing and containerization with Docker",
        "Agile software development methodologies",
        "RESTful API design best practices",
        "Object-oriented programming principles",
        "Version control with Git",
    ]
    for i, text in enumerate(texts):
        rec = KnowledgeMemory(
            id=f"doc-{i}",
            content={"text": text},
            owner="system",
            tags=["knowledge", "tech"],
            knowledge_type="fact",
            confidence=0.9,
        )
        await engine.store(rec)

    await retrieval.start()
    yield engine, retrieval
    await retrieval.stop()
    await engine.stop()


class TestRetrievalRequest:
    def test_defaults(self):
        req = RetrievalRequest()
        assert req.query == ""
        assert req.mode == RetrievalMode.HYBRID
        assert req.top_k == 10
        assert req.min_score == 0.0

    def test_custom(self):
        req = RetrievalRequest(
            query="test",
            mode=RetrievalMode.SEMANTIC,
            top_k=5,
            min_score=0.3,
        )
        assert req.query == "test"
        assert req.mode == RetrievalMode.SEMANTIC
        assert req.top_k == 5


class TestRetrievalResult:
    def test_to_dict(self):
        result = RetrievalResult(
            mode="hybrid",
            query="test",
            total_time_ms=5.0,
            semantic_count=3,
            text_count=2,
            duplicates_merged=1,
        )
        d = result.to_dict()
        assert d["mode"] == "hybrid"
        assert d["query"] == "test"
        assert d["total_results"] == 0


class TestSemanticRetrievalEngine:
    @pytest.mark.asyncio
    async def test_semantic_search(self, setup):
        engine, retrieval = setup
        result = await retrieval.semantic_search("Python programming")
        assert isinstance(result, RetrievalResult)
        assert result.mode == "semantic"
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_text_search(self, setup):
        engine, retrieval = setup
        result = await retrieval.text_search("Python")
        assert isinstance(result, RetrievalResult)
        assert result.mode == "text"

    @pytest.mark.asyncio
    async def test_hybrid_search(self, setup):
        engine, retrieval = setup
        result = await retrieval.hybrid_search("Python programming")
        assert isinstance(result, RetrievalResult)
        assert result.mode == "hybrid"
        # Hybrid should merge results from both sources
        assert result.semantic_count >= 0
        assert result.text_count >= 0

    @pytest.mark.asyncio
    async def test_retrieve_with_request(self, setup):
        engine, retrieval = setup
        req = RetrievalRequest(
            query="database SQL",
            mode=RetrievalMode.SEMANTIC,
            top_k=3,
        )
        result = await retrieval.retrieve(req)
        assert len(result.results) <= 3

    @pytest.mark.asyncio
    async def test_hybrid_deduplication(self, setup):
        engine, retrieval = setup
        result = await retrieval.hybrid_search("Python")
        # If a record appears in both semantic and text, it should be merged
        ids = [r.record.id for r in result.results]
        assert len(ids) == len(set(ids))  # No duplicates

    @pytest.mark.asyncio
    async def test_results_sorted_by_score(self, setup):
        engine, retrieval = setup
        result = await retrieval.hybrid_search("programming", top_k=5)
        scores = [r.final_score for r in result.results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_search_with_owner_filter(self, setup):
        engine, retrieval = setup
        result = await retrieval.semantic_search(
            "Python", owner="nonexistent"
        )
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_search_with_tags_filter(self, setup):
        engine, retrieval = setup
        result = await retrieval.semantic_search(
            "Python", tags=["tech"]
        )
        # Should return results (all records have "tech" tag)
        assert len(result.results) > 0

    @pytest.mark.asyncio
    async def test_search_with_type_filter(self, setup):
        engine, retrieval = setup
        result = await retrieval.semantic_search(
            "Python",
            memory_types=[MemoryType.PROJECT],
        )
        # No project memories in test data
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_for_context(self, setup):
        engine, retrieval = setup
        records = await retrieval.retrieve_for_context(
            query="machine learning",
            target="test-agent",
            top_k=3,
        )
        assert len(records) <= 3
        for r in records:
            assert isinstance(r, MemoryRecord)

    @pytest.mark.asyncio
    async def test_empty_query(self, setup):
        engine, retrieval = setup
        result = await retrieval.semantic_search("")
        # Should still work, just with no semantic match
        assert isinstance(result, RetrievalResult)

    @pytest.mark.asyncio
    async def test_no_semantic_index_fallback(self):
        """Retrieval should work without semantic index (text-only)."""
        engine = MemoryEngine()
        await engine.start()

        rec = MemoryRecord(
            content={"text": "test content with keyword"},
            tags=["test"],
        )
        await engine.store(rec)

        retrieval = SemanticRetrievalEngine(memory_engine=engine)
        await retrieval.start()

        result = await retrieval.text_search("keyword")
        assert result.text_count > 0

        # Semantic search should return empty
        result = await retrieval.semantic_search("keyword")
        assert result.semantic_count == 0

        await retrieval.stop()
        await engine.stop()

    @pytest.mark.asyncio
    async def test_total_time_ms_populated(self, setup):
        engine, retrieval = setup
        result = await retrieval.hybrid_search("test")
        assert result.total_time_ms >= 0

    @pytest.mark.asyncio
    async def test_ranked_result_to_dict(self, setup):
        engine, retrieval = setup
        result = await retrieval.hybrid_search("test", top_k=1)
        if result.results:
            d = result.results[0].to_dict()
            assert "id" in d
            assert "final_score" in d
            assert "signal_scores" in d
            assert "matched_by" in d


class TestRetrievalMode:
    def test_values(self):
        assert RetrievalMode.SEMANTIC.value == "semantic"
        assert RetrievalMode.TEXT.value == "text"
        assert RetrievalMode.HYBRID.value == "hybrid"