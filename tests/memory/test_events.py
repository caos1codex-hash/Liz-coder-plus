"""Tests for MemoryEventBus."""

import pytest

from packages.multiagent.src.multiagent.memctx.events import (
    MemoryEvent,
    MemoryEventBus,
    MemoryEventType,
)


class TestMemoryEvent:
    def test_create(self):
        ev = MemoryEvent(
            type=MemoryEventType.MEMORY_CREATED,
            source="test",
            payload={"id": "r1"},
        )
        assert ev.source == "test"
        assert ev.payload["id"] == "r1"
        assert ev.id
        assert ev.timestamp

    def test_to_dict(self):
        ev = MemoryEvent(
            type=MemoryEventType.MEMORY_CREATED,
            source="engine",
            payload={"key": "val"},
        )
        d = ev.to_dict()
        assert d["type"] == "memory.created"
        assert d["source"] == "engine"
        assert d["payload"] == {"key": "val"}
        assert "id" in d
        assert "timestamp" in d


class TestMemoryEventBusSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self):
        bus = MemoryEventBus()
        events = []

        async def handler(e):
            events.append(e)

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, handler)
        count = await bus.emit(MemoryEventType.MEMORY_CREATED, source="test")
        assert count == 1
        assert len(events) == 1
        assert events[0].source == "test"

    @pytest.mark.asyncio
    async def test_subscribe_returns_token(self):
        bus = MemoryEventBus()
        token = await bus.subscribe(
            MemoryEventType.MEMORY_CREATED,
            lambda e: None,
        )
        assert isinstance(token, str)
        assert len(token) == 8

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = MemoryEventBus()
        events = []

        async def handler(e):
            events.append(e)

        token = await bus.subscribe(MemoryEventType.MEMORY_CREATED, handler)
        await bus.unsubscribe(token)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self):
        bus = MemoryEventBus()
        assert await bus.unsubscribe("nope") is False

    @pytest.mark.asyncio
    async def test_subscribe_all(self):
        bus = MemoryEventBus()
        events = []

        async def handler(e):
            events.append(e)

        await bus.subscribe_all(handler)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_DELETED)
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        bus = MemoryEventBus()
        e1, e2 = [], []

        async def h1(e):
            e1.append(e)

        async def h2(e):
            e2.append(e)

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, h1)
        await bus.subscribe(MemoryEventType.MEMORY_CREATED, h2)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        assert len(e1) == 1
        assert len(e2) == 1

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_stop_others(self):
        bus = MemoryEventBus()
        events = []

        async def bad_handler(e):
            raise RuntimeError("boom")

        async def good_handler(e):
            events.append(e)

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, bad_handler)
        await bus.subscribe(MemoryEventType.MEMORY_CREATED, good_handler)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_emit_with_payload(self):
        bus = MemoryEventBus()
        events = []

        async def handler(e):
            events.append(e)

        await bus.subscribe(MemoryEventType.MEMORY_UPDATED, handler)
        await bus.emit(
            MemoryEventType.MEMORY_UPDATED,
            source="engine",
            payload={"id": "r1", "version": 2},
        )
        assert events[0].payload["id"] == "r1"
        assert events[0].payload["version"] == 2


class TestMemoryEventBusHistory:
    @pytest.mark.asyncio
    async def test_history_records_events(self):
        bus = MemoryEventBus()
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_DELETED)
        history = await bus.history()
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_history_filter_by_type(self):
        bus = MemoryEventBus()
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_DELETED)
        history = await bus.history(
            event_type=MemoryEventType.MEMORY_CREATED
        )
        assert len(history) == 1
        assert history[0]["type"] == "memory.created"

    @pytest.mark.asyncio
    async def test_history_limit(self):
        bus = MemoryEventBus()
        for _ in range(10):
            await bus.emit(MemoryEventType.MEMORY_CREATED)
        history = await bus.history(limit=3)
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_history_max_size(self):
        bus = MemoryEventBus(max_history=5)
        for i in range(10):
            await bus.emit(MemoryEventType.MEMORY_CREATED, payload={"i": i})
        history = await bus.history(limit=100)
        assert len(history) == 5
        # Should be the last 5
        assert history[-1]["payload"]["i"] == 9


class TestMemoryEventBusMetrics:
    @pytest.mark.asyncio
    async def test_metrics_published(self):
        bus = MemoryEventBus()
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_DELETED)
        m = bus.metrics()
        assert m["published"] == 2

    @pytest.mark.asyncio
    async def test_metrics_delivered(self):
        bus = MemoryEventBus()

        async def noop(e):
            pass

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, noop)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        m = bus.metrics()
        assert m["delivered"] == 1

    @pytest.mark.asyncio
    async def test_metrics_errors(self):
        bus = MemoryEventBus()
        async def bad(e):
            raise RuntimeError()

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, bad)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        m = bus.metrics()
        assert m["errors"] == 1

    @pytest.mark.asyncio
    async def test_metrics_by_type(self):
        bus = MemoryEventBus()
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.emit(MemoryEventType.MEMORY_DELETED)
        m = bus.metrics()
        assert m["by_type"]["memory.created"] == 2
        assert m["by_type"]["memory.deleted"] == 1

    @pytest.mark.asyncio
    async def test_clear(self):
        bus = MemoryEventBus()
        await bus.emit(MemoryEventType.MEMORY_CREATED)
        await bus.clear()
        history = await bus.history()
        assert len(history) == 0
        m = bus.metrics()
        assert m["published"] == 0

    @pytest.mark.asyncio
    async def test_emit_returns_handler_count(self):
        bus = MemoryEventBus()

        async def noop(e):
            pass

        await bus.subscribe(MemoryEventType.MEMORY_CREATED, noop)
        await bus.subscribe(MemoryEventType.MEMORY_CREATED, noop)
        count = await bus.emit(MemoryEventType.MEMORY_CREATED)
        assert count == 2

    @pytest.mark.asyncio
    async def test_string_event_type(self):
        bus = MemoryEventBus()
        events = []

        async def handler(e):
            events.append(e)

        await bus.subscribe("custom.event", handler)
        await bus.emit("custom.event", payload={"data": 1})
        assert len(events) == 1
        assert events[0].payload["data"] == 1