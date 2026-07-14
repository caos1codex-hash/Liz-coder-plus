"""Tests for the MemoryEngine."""

import pytest

from packages.multiagent.src.multiagent.memctx.engine import MemoryEngine
from packages.multiagent.src.multiagent.memctx.events import (
    MemoryEventBus,
    MemoryEventType,
)
from packages.multiagent.src.multiagent.memctx.metrics import MemoryMetrics
from packages.multiagent.src.multiagent.memctx.models import (
    MemoryRecord,
    MemoryType,
)
from packages.multiagent.src.multiagent.memctx.storage import InMemoryStorage


def _rec(id="r1", content="test", owner="a1", tags=None, **kw):
    return MemoryRecord(
        id=id, content=content, owner=owner, tags=tags or [], **kw
    )


class TestMemoryEngineLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = MemoryEngine()
        assert engine.is_started is False
        await engine.start()
        assert engine.is_started is True
        await engine.stop()
        assert engine.is_started is False

    @pytest.mark.asyncio
    async def test_start_twice(self):
        engine = MemoryEngine()
        await engine.start()
        await engine.start()  # should be idempotent
        assert engine.is_started is True
        await engine.stop()

    @pytest.mark.asyncio
    async def test_store_before_start_raises(self):
        engine = MemoryEngine()
        with pytest.raises(RuntimeError, match="not started"):
            await engine.store(_rec())

    @pytest.mark.asyncio
    async def test_custom_dependencies(self):
        bus = MemoryEventBus()
        metrics = MemoryMetrics()
        storage = InMemoryStorage()
        engine = MemoryEngine(
            storage=storage, event_bus=bus, metrics=metrics
        )
        assert engine.storage is storage
        assert engine.event_bus is bus
        assert engine.metrics is metrics


class TestMemoryEngineCRUD:
    @pytest.fixture
    async def engine(self):
        eng = MemoryEngine()
        await eng.start()
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_store(self, engine):
        rec = _rec("s1", "hello")
        saved = await engine.store(rec)
        assert saved.id == "s1"
        assert saved.version == 1

    @pytest.mark.asyncio
    async def test_store_duplicate_raises(self, engine):
        await engine.store(_rec("dup"))
        with pytest.raises(ValueError, match="duplicate"):
            await engine.store(_rec("dup"))

    @pytest.mark.asyncio
    async def test_retrieve(self, engine):
        await engine.store(_rec("r1"))
        got = await engine.retrieve("r1")
        assert got is not None
        assert got.content == "test"

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent(self, engine):
        assert await engine.retrieve("nope") is None

    @pytest.mark.asyncio
    async def test_update(self, engine):
        await engine.store(_rec("u1", "v1"))
        rec = await engine.retrieve("u1")
        rec.content = "v2"
        updated = await engine.update(rec)
        assert updated.content == "v2"
        assert updated.version == 2

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, engine):
        rec = _rec("nope")
        with pytest.raises(KeyError, match="not found"):
            await engine.update(rec)

    @pytest.mark.asyncio
    async def test_remove(self, engine):
        await engine.store(_rec("d1"))
        assert await engine.remove("d1") is True
        assert await engine.retrieve("d1") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, engine):
        assert await engine.remove("nope") is False

    @pytest.mark.asyncio
    async def test_exists(self, engine):
        await engine.store(_rec("e1"))
        assert await engine.exists("e1") is True
        assert await engine.exists("nope") is False


class TestMemoryEngineSearch:
    @pytest.fixture
    async def engine(self):
        eng = MemoryEngine()
        await eng.start()
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_search_by_tags(self, engine):
        await engine.store(_rec("a", tags=["python", "code"]))
        await engine.store(_rec("b", tags=["java", "code"]))
        await engine.store(_rec("c", tags=["design"]))
        results = await engine.search(tags=["code"])
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_by_owner(self, engine):
        await engine.store(_rec("a", owner="agent-1"))
        await engine.store(_rec("b", owner="agent-2"))
        results = await engine.search(owner="agent-1")
        assert len(results) == 1
        assert results[0].owner == "agent-1"

    @pytest.mark.asyncio
    async def test_search_by_type(self, engine):
        await engine.store(_rec("a", memory_type=MemoryType.CONVERSATION))
        await engine.store(_rec("b", memory_type=MemoryType.PROJECT))
        results = await engine.search(memory_type=MemoryType.CONVERSATION)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_by_query(self, engine):
        await engine.store(_rec("a", content="python programming guide"))
        results = await engine.search(query="python")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_list_records(self, engine):
        await engine.store(_rec("a"))
        await engine.store(_rec("b"))
        results = await engine.list_records()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_count(self, engine):
        await engine.store(_rec("a"))
        await engine.store(_rec("b"))
        assert await engine.count() == 2

    @pytest.mark.asyncio
    async def test_clear(self, engine):
        await engine.store(_rec("a"))
        await engine.store(_rec("b"))
        count = await engine.clear()
        assert count == 2
        assert await engine.count() == 0


class TestMemoryEngineBulk:
    @pytest.fixture
    async def engine(self):
        eng = MemoryEngine()
        await eng.start()
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_store_many(self, engine):
        records = [_rec(f"m{i}", f"content-{i}") for i in range(5)]
        results = await engine.store_many(records)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_store_many_skips_duplicates(self, engine):
        await engine.store(_rec("dup"))
        records = [_rec("dup"), _rec("new")]
        results = await engine.store_many(records)
        assert len(results) == 1
        assert results[0].id == "new"

    @pytest.mark.asyncio
    async def test_remove_many(self, engine):
        for i in range(5):
            await engine.store(_rec(f"d{i}"))
        count = await engine.remove_many(["d0", "d2", "d4"])
        assert count == 3
        assert await engine.count() == 2


class TestMemoryEngineConvenience:
    @pytest.fixture
    async def engine(self):
        eng = MemoryEngine()
        await eng.start()
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_store_conversation(self, engine):
        rec = await engine.store_conversation(
            session_id="s1", role="user", text="Hello!"
        )
        assert rec.memory_type == MemoryType.CONVERSATION
        assert "s1" in rec.tags

    @pytest.mark.asyncio
    async def test_store_project(self, engine):
        rec = await engine.store_project(
            project_id="p1", description="Use React"
        )
        assert rec.memory_type == MemoryType.PROJECT
        assert "p1" in rec.tags

    @pytest.mark.asyncio
    async def test_store_workflow(self, engine):
        rec = await engine.store_workflow(
            workflow_id="w1", step_id="s1", result="success"
        )
        assert rec.memory_type == MemoryType.WORKFLOW
        assert "w1" in rec.tags

    @pytest.mark.asyncio
    async def test_store_knowledge(self, engine):
        rec = await engine.store_knowledge(
            text="Python 3.11+ required", source="docs"
        )
        assert rec.memory_type == MemoryType.KNOWLEDGE
        assert rec.content["text"] == "Python 3.11+ required"


class TestMemoryEngineEvents:
    @pytest.fixture
    async def engine(self):
        bus = MemoryEventBus()
        eng = MemoryEngine(event_bus=bus)
        await eng.start()
        yield eng, bus
        await eng.stop()

    @pytest.mark.asyncio
    async def test_store_emits_created_event(self, engine):
        eng, bus = engine
        events = []
        await bus.subscribe(MemoryEventType.MEMORY_CREATED, lambda e: events.append(e))
        await eng.store(_rec("ev1"))
        assert len(events) == 1
        assert events[0].payload["id"] == "ev1"

    @pytest.mark.asyncio
    async def test_update_emits_updated_event(self, engine):
        eng, bus = engine
        events = []
        await bus.subscribe(MemoryEventType.MEMORY_UPDATED, lambda e: events.append(e))
        await eng.store(_rec("ev2"))
        rec = await eng.retrieve("ev2")
        rec.content = "changed"
        await eng.update(rec)
        assert len(events) == 1
        assert events[0].payload["version"] == 2

    @pytest.mark.asyncio
    async def test_delete_emits_deleted_event(self, engine):
        eng, bus = engine
        events = []
        await bus.subscribe(MemoryEventType.MEMORY_DELETED, lambda e: events.append(e))
        await eng.store(_rec("ev3"))
        await eng.remove("ev3")
        assert len(events) == 1
        assert events[0].payload["id"] == "ev3"


class TestMemoryEngineHealth:
    @pytest.fixture
    async def engine(self):
        eng = MemoryEngine()
        await eng.start()
        yield eng
        await eng.stop()

    @pytest.mark.asyncio
    async def test_health_check(self, engine):
        await engine.store(_rec("h1"))
        health = await engine.health_check()
        assert health["status"] == "healthy"
        assert health["total_records"] == 1
        assert health["indexed_records"] == 1

    @pytest.mark.asyncio
    async def test_health_check_stopped(self):
        engine = MemoryEngine()
        health = await engine.health_check()
        assert health["status"] == "stopped"