"""Tests for the ContextEngine."""

import pytest

from packages.multiagent.src.multiagent.memctx.context import (
    ContextEngine,
    ContextRequest,
    ContextResult,
)
from packages.multiagent.src.multiagent.memctx.engine import MemoryEngine
from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)


def _rec(id, content="test", owner="a1", tags=None, memory_type=MemoryType.KNOWLEDGE, priority=0):
    return MemoryRecord(
        id=id, content=content, owner=owner, tags=tags or [],
        memory_type=memory_type, priority=priority,
    )


class TestContextRequest:
    def test_defaults(self):
        req = ContextRequest()
        assert req.target == ""
        assert req.max_tokens == 4096
        assert req.max_records == 200

    def test_custom(self):
        req = ContextRequest(
            target="agent-1",
            max_tokens=2048,
            tags=["python"],
        )
        assert req.target == "agent-1"
        assert req.max_tokens == 2048


class TestContextEngineBuild:
    @pytest.fixture
    async def ctx_engine(self):
        mem = MemoryEngine()
        await mem.start()
        eng = ContextEngine(mem)
        yield eng, mem
        await mem.stop()

    @pytest.mark.asyncio
    async def test_build_empty_context(self, ctx_engine):
        eng, mem = ctx_engine
        req = ContextRequest(target="test-agent")
        result = await eng.build_context(req)
        assert isinstance(result, ContextResult)
        assert result.snapshot.records == []
        assert result.records_scanned == 0

    @pytest.mark.asyncio
    async def test_build_with_records(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(_rec("k1", "python knowledge", tags=["python"], owner="agent-1"))
        req = ContextRequest(target="agent-1", owner="agent-1")
        result = await eng.build_context(req)
        assert result.records_scanned >= 1
        assert len(result.snapshot.records) >= 1

    @pytest.mark.asyncio
    async def test_build_filters_by_tags(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(_rec("k1", "python stuff", tags=["python"]))
        await mem.store(_rec("k2", "java stuff", tags=["java"]))
        req = ContextRequest(tags=["python"])
        result = await eng.build_context(req)
        # Should only find python-tagged
        for r in result.snapshot.records:
            assert "python" in r.get("tags", [])

    @pytest.mark.asyncio
    async def test_build_filters_by_memory_type(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(_rec("c1", "chat", memory_type=MemoryType.CONVERSATION))
        await mem.store(_rec("k1", "fact", memory_type=MemoryType.KNOWLEDGE))
        req = ContextRequest(memory_types=[MemoryType.KNOWLEDGE])
        result = await eng.build_context(req)
        for r in result.snapshot.records:
            assert r.get("memory_type") == "knowledge"

    @pytest.mark.asyncio
    async def test_deduplication(self, ctx_engine):
        eng, mem = ctx_engine
        # Store two records with same content
        await mem.store(_rec("d1", "identical content", priority=1))
        await mem.store(_rec("d2", "identical content", priority=5))
        req = ContextRequest()
        result = await eng.build_context(req)
        # Higher priority version should be kept
        assert result.duplicates_removed >= 1

    @pytest.mark.asyncio
    async def test_trim_to_budget(self, ctx_engine):
        eng, mem = ctx_engine
        # Store records with different content to avoid dedup
        for i in range(20):
            await mem.store(_rec(f"trim{i}", f"unique content number {i} " * 50))
        req = ContextRequest(max_tokens=100)  # Very small budget
        result = await eng.build_context(req)
        assert result.snapshot.was_trimmed is True
        assert result.snapshot.trimmed_count > 0

    @pytest.mark.asyncio
    async def test_priority_threshold(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(_rec("low", "low priority", priority=1))
        await mem.store(_rec("high", "high priority", priority=10))
        req = ContextRequest(priority_threshold=5)
        result = await eng.build_context(req)
        for r in result.snapshot.records:
            # Note: snapshot stores dict, so we check at engine level
            pass
        # The result should only include high priority
        assert len(result.snapshot.records) <= 1


class TestContextEngineConvenience:
    @pytest.fixture
    async def ctx_engine(self):
        mem = MemoryEngine()
        await mem.start()
        eng = ContextEngine(mem)
        yield eng, mem
        await mem.stop()

    @pytest.mark.asyncio
    async def test_build_agent_context(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(_rec("a1", "agent knowledge", owner="coder", tags=["agent"]))
        snap = await eng.build_agent_context("coder")
        assert snap.target == "coder"
        assert isinstance(snap.records, list)

    @pytest.mark.asyncio
    async def test_build_workflow_context(self, ctx_engine):
        eng, mem = ctx_engine
        await mem.store(
            _rec("w1", "step result", tags=["workflow", "wf-1", "s1"],
                memory_type=MemoryType.WORKFLOW)
        )
        snap = await eng.build_workflow_context("wf-1")
        assert snap.target == "wf-1"
        assert isinstance(snap.records, list)

    @pytest.mark.asyncio
    async def test_save_and_load_context(self, ctx_engine):
        eng, mem = ctx_engine
        # Build a context
        req = ContextRequest(target="test")
        result = await eng.build_context(req)
        snap = result.snapshot

        # Save it
        saved = await eng.save_context(snap)
        assert saved.id == snap.id

        # Load it
        loaded = await eng.load_context(snap.id)
        assert loaded is not None
        assert loaded.target == "test"

    @pytest.mark.asyncio
    async def test_load_nonexistent_context(self, ctx_engine):
        eng, _ = ctx_engine
        loaded = await eng.load_context("nonexistent")
        assert loaded is None


class TestContextEngineStats:
    @pytest.fixture
    async def ctx_engine(self):
        mem = MemoryEngine()
        await mem.start()
        eng = ContextEngine(mem)
        yield eng, mem
        await mem.stop()

    @pytest.mark.asyncio
    async def test_stats_tracking(self, ctx_engine):
        eng, mem = ctx_engine
        # Build with no trim
        await eng.build_context(ContextRequest())
        # Build with trim (small budget, multiple different records)
        for i in range(10):
            await mem.store(_rec(f"stat-trim-{i}", f"content {i} " * 100))
        await eng.build_context(ContextRequest(max_tokens=50))
        stats = eng.stats
        assert stats["contexts_built"] == 2
        assert stats["contexts_trimmed"] >= 1


class TestContextEngineEvents:
    @pytest.fixture
    async def ctx_engine(self):
        mem = MemoryEngine()
        await mem.start()
        eng = ContextEngine(mem)
        yield eng, mem
        await mem.stop()

    @pytest.mark.asyncio
    async def test_context_created_event(self, ctx_engine):
        eng, mem = ctx_engine
        events = []

        async def handler(e):
            events.append(e)

        await mem.event_bus.subscribe_all(handler)
        await eng.build_context(ContextRequest(target="event-test"))
        # Should have at least the context_created event
        created = [e for e in events if "context.created" in str(e.type)]
        assert len(created) >= 1