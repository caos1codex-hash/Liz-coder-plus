"""Tests para mensajes y MessageBus (Sprint 2.1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.enums import MessageType, Priority  # noqa: E402
from multiagent.messages import Message, MessageBus  # noqa: E402


# --- Message construction ---


def test_message_basic_construction() -> None:
    m = Message(
        sender="planner",
        receiver="coder",
        type=MessageType.REQUEST,
        payload={"task": "write"},
    )
    assert m.sender == "planner"
    assert m.receiver == "coder"
    assert m.type == MessageType.REQUEST
    assert m.payload == {"task": "write"}
    assert m.priority == Priority.NORMAL
    assert m.id  # auto-generated
    assert m.timestamp  # auto-generated


def test_message_request_factory() -> None:
    m = Message.request("a", "b", {"k": 1})
    assert m.type == MessageType.REQUEST
    assert m.sender == "a"
    assert m.receiver == "b"
    assert m.payload == {"k": 1}


def test_message_response_factory() -> None:
    m = Message.response("a", "b", {"result": 42}, correlation_id="c1")
    assert m.type == MessageType.RESPONSE
    assert m.correlation_id == "c1"


def test_message_event_factory() -> None:
    m = Message.event("a", "b", "started", {"when": "now"})
    assert m.type == MessageType.EVENT
    assert m.payload == {"name": "started", "data": {"when": "now"}}


def test_message_error_factory() -> None:
    m = Message.error("a", "b", "boom", code="X")
    assert m.type == MessageType.ERROR
    assert m.priority == Priority.HIGH
    assert m.payload == {"error": "boom", "code": "X"}


def test_message_system_factory() -> None:
    m = Message.system("a", "b", {"note": "hi"})
    assert m.type == MessageType.SYSTEM


def test_message_broadcast_detection() -> None:
    m = Message.system("a", "*", {"note": "hi"})
    assert m.is_broadcast is True

    m2 = Message.system("a", "b", {})
    assert m2.is_broadcast is False


def test_message_serialization_roundtrip() -> None:
    m = Message.request("a", "b", {"k": 1}, priority=Priority.HIGH, correlation_id="c1")
    d = m.to_dict()
    assert d["sender"] == "a"
    assert d["type"] == "request"
    assert d["priority"] == "high"

    m2 = Message.from_dict(d)
    assert m2.sender == m.sender
    assert m2.receiver == m.receiver
    assert m2.type == m.type
    assert m2.priority == m.priority
    assert m2.payload == m.payload
    assert m2.id == m.id


# --- MessageBus ---


@pytest.mark.asyncio
async def test_bus_subscribe_and_publish() -> None:
    bus = MessageBus()
    received: list[Message] = []

    async def handler(m: Message) -> None:
        received.append(m)

    token = await bus.subscribe("planner", handler)
    msg = Message.request("orchestrator", "planner", {"k": 1})
    delivered = await bus.publish(msg)
    assert delivered == 1
    assert len(received) == 1
    assert received[0].id == msg.id
    await bus.unsubscribe(token)


@pytest.mark.asyncio
async def test_bus_pending_when_no_subscribers() -> None:
    bus = MessageBus()
    msg = Message.request("a", "nonexistent", {"k": 1})
    delivered = await bus.publish(msg)
    assert delivered == 0
    # El mensaje quedó encolado en pending.
    assert bus.stats()["pending_messages"] == 1


@pytest.mark.asyncio
async def test_bus_dispatch_pending() -> None:
    bus = MessageBus()
    received: list[Message] = []

    async def handler(m: Message) -> None:
        received.append(m)

    # Publicar antes de suscribir.
    msg = Message.request("a", "planner", {"k": 1})
    await bus.publish(msg)
    assert len(received) == 0

    # Suscribir y disparar la entrega pendiente.
    await bus.subscribe("planner", handler)
    delivered = await bus.dispatch_pending("planner")
    assert delivered == 1
    assert len(received) == 1


@pytest.mark.asyncio
async def test_bus_broadcast() -> None:
    bus = MessageBus()
    received: list[Message] = []

    async def handler(m: Message) -> None:
        received.append(m)

    await bus.subscribe_broadcast(handler)
    await bus.subscribe_broadcast(handler)  # 2 suscriptores
    msg = Message.system("a", "*", {"hello": "all"})
    delivered = await bus.publish(msg)
    assert delivered == 2
    assert len(received) == 2


@pytest.mark.asyncio
async def test_bus_handler_error_isolated() -> None:
    bus = MessageBus()

    async def bad_handler(m: Message) -> None:
        raise RuntimeError("boom")

    received: list[Message] = []

    async def good_handler(m: Message) -> None:
        received.append(m)

    await bus.subscribe("planner", bad_handler)
    await bus.subscribe("planner", good_handler)
    msg = Message.request("a", "planner", {})
    delivered = await bus.publish(msg)
    # El segundo handler recibió el mensaje aunque el primero falle.
    assert delivered == 1
    assert len(received) == 1


@pytest.mark.asyncio
async def test_bus_stats() -> None:
    bus = MessageBus()
    received: list[Message] = []

    async def handler(m: Message) -> None:
        received.append(m)

    await bus.subscribe("planner", handler)
    for i in range(5):
        await bus.publish(Message.request("a", "planner", {"i": i}))
    stats = bus.stats()
    assert stats["published"] == 5
    assert stats["delivered"] == 5
    assert stats["subscribers"] == 1


@pytest.mark.asyncio
async def test_bus_unsubscribe() -> None:
    bus = MessageBus()
    received: list[Message] = []

    async def handler(m: Message) -> None:
        received.append(m)

    token = await bus.subscribe("planner", handler)
    assert await bus.unsubscribe(token) is True
    # Re-unsubscribe returns False.
    assert await bus.unsubscribe(token) is False

    await bus.publish(Message.request("a", "planner", {}))
    assert len(received) == 0


@pytest.mark.asyncio
async def test_bus_clear() -> None:
    bus = MessageBus()

    async def handler(m: Message) -> None:
        pass

    await bus.subscribe("planner", handler)
    await bus.subscribe_broadcast(handler)
    await bus.publish(Message.request("a", "planner", {}))
    assert bus.stats()["subscribers"] == 1
    await bus.clear()
    assert bus.stats()["subscribers"] == 0
