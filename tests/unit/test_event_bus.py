"""Unit tests for the EventBus.

Sprint 1.5 - Phase 6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402


@pytest.mark.asyncio
async def test_subscribe_and_publish() -> None:
    bus = EventBus()
    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("test.event", handler)
    count = await bus.publish("test.event", {"key": "value"})

    assert count == 1
    assert received == [{"key": "value"}]


@pytest.mark.asyncio
async def test_multiple_subscribers() -> None:
    bus = EventBus()
    order = []

    async def handler_a(payload):
        order.append("a")

    async def handler_b(payload):
        order.append("b")

    bus.subscribe("test", handler_a)
    bus.subscribe("test", handler_b)
    count = await bus.publish("test")

    assert count == 2
    assert order == ["a", "b"]


@pytest.mark.asyncio
async def test_handler_failure_does_not_block_others() -> None:
    bus = EventBus()
    received = []

    async def bad_handler(payload):
        raise RuntimeError("boom")

    async def good_handler(payload):
        received.append("ok")

    bus.subscribe("test", bad_handler)
    bus.subscribe("test", good_handler)
    count = await bus.publish("test")

    # Both were invoked; bad one failed silently.
    assert count == 1
    assert received == ["ok"]


@pytest.mark.asyncio
async def test_unsubscribe() -> None:
    bus = EventBus()
    received = []

    async def handler(payload):
        received.append(1)

    bus.subscribe("test", handler)
    bus.unsubscribe("test", handler)
    count = await bus.publish("test")

    assert count == 0
    assert received == []


def test_subscribers_count() -> None:
    bus = EventBus()
    assert bus.subscribers("nonexistent") == 0

    async def h(p): pass
    bus.subscribe("e", h)
    assert bus.subscribers("e") == 1


def test_event_types() -> None:
    bus = EventBus()
    assert bus.event_types() == []

    async def h(p): pass
    bus.subscribe("a.b", h)
    bus.subscribe("c.d", h)
    types = bus.event_types()
    assert "a.b" in types
    assert "c.d" in types


def test_clear() -> None:
    bus = EventBus()

    async def h(p): pass
    bus.subscribe("test", h)
    bus.clear()
    assert bus.subscribers("test") == 0
    assert bus.event_types() == []


@pytest.mark.asyncio
async def test_publish_to_nonexistent_event() -> None:
    bus = EventBus()
    count = await bus.publish("nonexistent")
    assert count == 0