"""Tests for context integration with semantic retrieval."""

import pytest

from multiagent.memctx.context import ContextEngine, ContextRequest
from multiagent.memctx.engine import MemoryEngine
from multiagent.memctx.models import KnowledgeMemory, MemoryRecord, MemoryType
from multiagent.semantic.cache import EmbeddingCache
from multiagent.semantic.embedding import DummyEmbeddingProvider
from multiagent.semantic.retrieval import SemanticRetrievalEngine
from multiagent.semantic.semantic_index import SemanticIndex
from multiagent.semantic.vector_store import InMemoryVectorStore


@pytest.fixture
async def full_setup():
    """Create a fully wired system: engine + context + semantic retrieval."""
    engine = MemoryEngine()
    await engine.start()

    provider = DummyEmbeddingProvider(dims=32, seed=42)
    store = InMemoryVectorStore()
    cache = EmbeddingCache()

    sem_index = SemanticIndex(provider, store, cache, event_bus=engine.event_bus)
    retrieval = SemanticRetrievalEngine(
        memory_engine=engine,
        embedding_provider=provider,
        semantic_index=sem_index,
        event_bus=engine.event_bus,
        cache=cache,
    )

    # Store test data
    texts = [
        "Python programming language fundamentals and best practices",
        "Machine learning algorithms and neural network architectures",
        "Web development with modern JavaScript frameworks",
        "Database optimization techniques and query tuning",
        "Python data analysis with pandas and numpy",
    ]
    for i, text in enumerate(texts):
        rec = KnowledgeMemory(
            id=f"doc-{i}",
            content={"text": text},
            owner="system",
            tags=["knowledge", "tech"],
        )
        await engine.store(rec)

    await retrieval.start()

    # Create context engine WITH semantic retrieval
    ctx_engine = ContextEngine(engine, semantic_retrieval=retrieval)

    # Also create one WITHOUT semantic for comparison
    ctx_engine_plain = ContextEngine(engine)

    yield engine, ctx_engine, ctx_engine_plain, retrieval

    await retrieval.stop()
    await engine.stop()


class TestContextSemanticIntegration:
    @pytest.mark.asyncio
    async def test_plain_context_still_works(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        req = ContextRequest(
            target="test-agent",
            memory_types=[MemoryType.KNOWLEDGE],
            max_tokens=1000,
        )
        result = await ctx_plain.build_context(req)
        assert result.snapshot.records
        assert result.records_scanned > 0

    @pytest.mark.asyncio
    async def test_semantic_context_with_query(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        req = ContextRequest(
            target="test-agent",
            query="Python programming",
            use_semantic=True,
            memory_types=[MemoryType.KNOWLEDGE],
            max_tokens=2000,
        )
        result = await ctx_engine.build_context(req)
        assert result.snapshot.records
        assert result.build_time_ms >= 0

    @pytest.mark.asyncio
    async def test_semantic_context_no_query_uses_text_only(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        req = ContextRequest(
            target="test-agent",
            use_semantic=True,
            # No query - should fall back to text search
            memory_types=[MemoryType.KNOWLEDGE],
            max_tokens=1000,
        )
        result = await ctx_engine.build_context(req)
        # Should still work, just without semantic enrichment
        assert isinstance(result.snapshot.records, list)

    @pytest.mark.asyncio
    async def test_context_without_semantic_retrieval(self):
        """ContextEngine without semantic retrieval should work as before."""
        engine = MemoryEngine()
        await engine.start()

        rec = MemoryRecord(
            content={"text": "test content"},
            tags=["test"],
        )
        await engine.store(rec)

        ctx = ContextEngine(engine)  # No semantic retrieval
        req = ContextRequest(target="agent", query="test")
        result = await ctx.build_context(req)
        assert result.records_scanned >= 0

        await engine.stop()

    @pytest.mark.asyncio
    async def test_set_semantic_retrieval(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        # Start without semantic
        ctx = ContextEngine(engine)
        assert ctx.stats["semantic_contexts"] == 0

        # Set semantic retrieval
        ctx.set_semantic_retrieval(retrieval)

        # Now use it
        req = ContextRequest(
            target="agent",
            query="Python",
            use_semantic=True,
            memory_types=[MemoryType.KNOWLEDGE],
        )
        await ctx.build_context(req)
        assert ctx.stats["semantic_contexts"] == 1

    @pytest.mark.asyncio
    async def test_semantic_context_stats(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        stats = ctx_engine.stats
        assert "contexts_built" in stats
        assert "contexts_trimmed" in stats
        assert "semantic_contexts" in stats

    @pytest.mark.asyncio
    async def test_build_agent_context_unchanged(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        snapshot = await ctx_plain.build_agent_context("coder-agent")
        assert isinstance(snapshot.records, list)

    @pytest.mark.asyncio
    async def test_build_workflow_context_unchanged(self, full_setup):
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        snapshot = await ctx_plain.build_workflow_context("wf-123", "step-1")
        assert isinstance(snapshot.records, list)

    @pytest.mark.asyncio
    async def test_semantic_failure_fallback(self, full_setup):
        """If semantic retrieval fails, context should fall back to text."""
        engine, ctx_engine, ctx_plain, retrieval = full_setup

        # Create a context engine with a broken semantic retrieval
        class BrokenRetrieval:
            async def retrieve_for_context(self, **kwargs):
                raise RuntimeError("Provider unavailable")

        ctx = ContextEngine(engine, semantic_retrieval=BrokenRetrieval())
        req = ContextRequest(
            target="agent",
            query="test",
            use_semantic=True,
            memory_types=[MemoryType.KNOWLEDGE],
            max_tokens=1000,
        )
        # Should NOT raise — should fall back to text search
        result = await ctx.build_context(req)
        assert isinstance(result.snapshot.records, list)

    @pytest.mark.asyncio
    async def test_context_request_new_fields(self):
        req = ContextRequest(
            use_semantic=True,
            semantic_top_k=15,
        )
        assert req.use_semantic is True
        assert req.semantic_top_k == 15

    @pytest.mark.asyncio
    async def test_deduplication_across_sources(self, full_setup):
        """Records from both semantic and text should be deduplicated."""
        engine, ctx_engine, ctx_plain, retrieval = full_setup
        req = ContextRequest(
            target="agent",
            query="Python",
            use_semantic=True,
            memory_types=[MemoryType.KNOWLEDGE],
            max_tokens=5000,
        )
        result = await ctx_engine.build_context(req)
        ids = [r.get("id") for r in result.snapshot.records]
        assert len(ids) == len(set(ids))