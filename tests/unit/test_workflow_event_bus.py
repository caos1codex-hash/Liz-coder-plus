"""Tests para EventBus (Sprint 2.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.workflow.enums import WorkflowEvent  # noqa: E402
from multiagent.workflow.event_bus import Event, EventBus  # noqa: E402


# --- Event ---


def test_event_creation() -> None:
    e = Event(
        type=WorkflowEvent.WORKFLOW_CREATED,
        source="engine",
        payload={"k": 1},
    )
    assert e.type == WorkflowEvent.WORKFLOW_CREATED
    assert e.type_str == "workflow.created"
    assert e.source == "engine"
    assert e.payload == {"k": 1}
    assert e.id
    assert e.timestamp
    assert e.sequence == 0


def test_event_serialization() -> None:
    e = Event(type="custom.event", source="x", payload={"a": 1})
    d = e.to_dict()
    assert d["type"] == "custom.event"
    assert d["source"] == "x"
    assert d["payload"] == {"a": 1}


# --- EventBus subscribe/publish ---


@pytest.mark.asyncio
async def test_subscribe_and_publish() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    await bus.subscribe(handler, event_type=WorkflowEvent.WORKFLOW_CREATED)
    await bus.publish(
        WorkflowEvent.WORKFLOW_CREATED,
        source="engine",
        payload={"id": "wf1"},
    )
    assert len(received) == 1
    assert received[0].payload == {"id": "wf1"}


@pytest.mark.asyncio
async def test_subscribe_global_receives_all_events() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    # Sin event_type = suscripción global.
    await bus.subscribe(handler)
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a")
    await bus.publish(WorkflowEvent.STEP_STARTED, source="b")
    await bus.publish("custom.event", source="c")
    assert len(received) == 3


@pytest.mark.asyncio
async def test_filter_fn() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    # Filtrar sólo eventos con payload que tenga key "important".
    await bus.subscribe(
        handler,
        filter_fn=lambda e: e.payload.get("important") is True,
    )
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a", payload={"important": True})
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="b", payload={"important": False})
    assert len(received) == 1
    assert received[0].source == "a"


@pytest.mark.asyncio
async def test_unsubscribe() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    token = await bus.subscribe(handler, event_type=WorkflowEvent.WORKFLOW_STARTED)
    await bus.unsubscribe(token)
    await bus.publish(WorkflowEvent.WORKFLOW_STARTED, source="a")
    assert len(received) == 0


@pytest.mark.asyncio
async def test_handler_error_isolated() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def bad(e: Event) -> None:
        raise RuntimeError("boom")

    async def good(e: Event) -> None:
        received.append(e)

    await bus.subscribe(bad, event_type=WorkflowEvent.WORKFLOW_STARTED)
    await bus.subscribe(good, event_type=WorkflowEvent.WORKFLOW_STARTED)
    await bus.publish(WorkflowEvent.WORKFLOW_STARTED, source="a")
    # El handler bueno recibió el evento aunque el malo fallara.
    assert len(received) == 1
    assert bus.metrics()["errors"] == 1


@pytest.mark.asyncio
async def test_history() -> None:
    bus = EventBus(history_size=100)
    for i in range(5):
        await bus.publish(
            WorkflowEvent.WORKFLOW_CREATED,
            source="x",
            payload={"i": i},
        )
    history = bus.history(limit=10)
    assert len(history) == 5
    # Orden cronológico.
    assert history[0].payload["i"] == 0
    assert history[4].payload["i"] == 4


@pytest.mark.asyncio
async def test_history_filtered_by_type() -> None:
    bus = EventBus()
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a")
    await bus.publish(WorkflowEvent.STEP_STARTED, source="b")
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="c")
    history = bus.history(event_type=WorkflowEvent.WORKFLOW_CREATED)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_history_filtered_by_correlation_id() -> None:
    bus = EventBus()
    await bus.publish(
        WorkflowEvent.WORKFLOW_STARTED,
        source="a",
        correlation_id="wf1",
    )
    await bus.publish(
        WorkflowEvent.STEP_STARTED,
        source="b",
        correlation_id="wf1",
    )
    await bus.publish(
        WorkflowEvent.WORKFLOW_STARTED,
        source="c",
        correlation_id="wf2",
    )
    history = bus.history(correlation_id="wf1")
    assert len(history) == 2


@pytest.mark.asyncio
async def test_metrics() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    await bus.subscribe(handler, event_type=WorkflowEvent.WORKFLOW_CREATED)
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a")
    await bus.publish(WorkflowEvent.STEP_STARTED, source="b")
    m = bus.metrics()
    assert m["published"] == 2
    assert m["delivered"] == 1  # sólo WORKFLOW_CREATED fue entregado
    assert m["subscriber_count"] == 1
    assert "workflow.created" in m["by_type"]
    assert "step.started" in m["by_type"]


@pytest.mark.asyncio
async def test_clear() -> None:
    bus = EventBus()

    async def handler(e: Event) -> None:
        pass

    await bus.subscribe(handler)
    await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a")
    assert bus.metrics()["published"] == 1
    await bus.clear()
    assert bus.metrics()["published"] == 0
    assert bus.metrics()["subscriber_count"] == 0


@pytest.mark.asyncio
async def test_sequence_monotonic() -> None:
    bus = EventBus()
    e1 = await bus.publish(WorkflowEvent.WORKFLOW_CREATED, source="a")
    e2 = await bus.publish(WorkflowEvent.WORKFLOW_STARTED, source="b")
    e3 = await bus.publish(WorkflowEvent.WORKFLOW_COMPLETED, source="c")
    assert e1.sequence < e2.sequence < e3.sequence


@pytest.mark.asyncio
async def test_correlation_id_propagates() -> None:
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    await bus.subscribe(handler)
    await bus.publish(
        WorkflowEvent.WORKFLOW_STARTED,
        source="a",
        correlation_id="wf-123",
    )
    assert received[0].correlation_id == "wf-123"
