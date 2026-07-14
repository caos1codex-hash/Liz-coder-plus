"""Unit tests for communication.bus (Sprint 2.9)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMM_SRC = ROOT / "packages" / "communication" / "src"
sys.path.insert(0, str(COMM_SRC))

for _k in [k for k in list(sys.modules) if k == "communication" or k.startswith("communication.")]:
    sys.modules.pop(_k, None)

from communication import (  # noqa: E402
    AgentMessage,
    AgentMessageBus,
    BusConfig,
    InMemoryMessageRepository,
    MessagePriority,
    MessageType,
)

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_construction():
    bus = AgentMessageBus()
    assert bus.config is not None
    assert bus.repository is not None
    assert bus.underlying_bus is not None


def test_custom_config():
    cfg = BusConfig(enforce_ttl=False, persist_messages=False, default_ttl=60.0)
    bus = AgentMessageBus(config=cfg)
    assert bus.config.enforce_ttl is False
    assert bus.config.persist_messages is False
    assert bus.config.default_ttl == 60.0


def test_custom_repository():
    repo = InMemoryMessageRepository()
    bus = AgentMessageBus(repository=repo)
    assert bus.repository is repo


# ----------------------------------------------------------------------
# Subscribe + publish
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    msg = AgentMessage.task_request("planner", "coder", payload={"x": 1})
    n = await bus.publish(msg)
    assert n == 1
    assert len(received) == 1
    assert received[0].payload["x"] == 1


@pytest.mark.asyncio
async def test_publish_no_subscriber():
    bus = AgentMessageBus()
    msg = AgentMessage.task_request("planner", "coder")
    n = await bus.publish(msg)
    assert n == 0


@pytest.mark.asyncio
async def test_subscribe_broadcast():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe_broadcast(handler)
    msg = AgentMessage.status_update("coder", status="running")
    n = await bus.publish(msg)
    assert n >= 1
    assert len(received) == 1


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    token = await bus.subscribe("coder", handler)
    ok = await bus.unsubscribe(token)
    assert ok is True
    await bus.publish(AgentMessage.task_request("planner", "coder"))
    assert len(received) == 0


@pytest.mark.asyncio
async def test_unsubscribe_unknown():
    bus = AgentMessageBus()
    ok = await bus.unsubscribe("nonexistent-token")
    assert ok is False


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = AgentMessageBus()
    received_a: list[AgentMessage] = []
    received_b: list[AgentMessage] = []

    async def handler_a(msg):
        received_a.append(msg)

    async def handler_b(msg):
        received_b.append(msg)

    await bus.subscribe("coder", handler_a)
    await bus.subscribe("coder", handler_b)
    n = await bus.publish(AgentMessage.task_request("planner", "coder"))
    assert n == 2
    assert len(received_a) == 1
    assert len(received_b) == 1


# ----------------------------------------------------------------------
# Convenience senders
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_task_request():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    msg = await bus.send_task_request("planner", "coder", payload={"x": 1})
    assert msg.message_type == MessageType.TASK_REQUEST
    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_task_response():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("planner", handler)
    msg = await bus.send_task_response(
        "coder",
        "planner",
        payload={"result": "ok"},
        in_reply_to="msg-123",
    )
    assert msg.message_type == MessageType.TASK_RESPONSE
    assert msg.in_reply_to == "msg-123"
    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_status_update():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe_broadcast(handler)
    msg = await bus.send_status_update("coder", status="running", progress=0.5)
    assert msg.message_type == MessageType.STATUS_UPDATE
    assert msg.is_broadcast
    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_error_report():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("orchestrator", handler)
    msg = await bus.send_error_report("coder", error="boom")
    assert msg.message_type == MessageType.ERROR_REPORT
    assert msg.priority == MessagePriority.HIGH
    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_handoff():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    msg = await bus.send_handoff("planner", "coder", task_id="t1", reason="specialized")
    assert msg.message_type == MessageType.HANDOFF
    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_context_transfer():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    msg = await bus.send_context_transfer(
        "planner",
        "coder",
        context={"files": ["a.py"]},
    )
    assert msg.message_type == MessageType.CONTEXT_TRANSFER
    assert msg.payload["context"] == {"files": ["a.py"]}
    assert len(received) == 1


@pytest.mark.asyncio
async def test_publish_many():
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    messages = [AgentMessage.task_request("planner", "coder", payload={"i": i}) for i in range(3)]
    results = await bus.publish_many(messages)
    assert len(results) == 3
    assert len(received) == 3


# ----------------------------------------------------------------------
# TTL enforcement
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_message_dropped():
    from datetime import datetime, timezone, timedelta

    bus = AgentMessageBus(config=BusConfig(enforce_ttl=True))
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    past = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat(timespec="seconds")
    msg = AgentMessage(
        sender_agent="planner",
        receiver_agent="coder",
        timestamp=past,
        ttl=1.0,
    )
    n = await bus.publish(msg)
    assert n == 0
    assert len(received) == 0


@pytest.mark.asyncio
async def test_ttl_enforcement_disabled():
    from datetime import datetime, timezone, timedelta

    bus = AgentMessageBus(config=BusConfig(enforce_ttl=False))
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)
    past = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat(timespec="seconds")
    msg = AgentMessage(
        sender_agent="planner",
        receiver_agent="coder",
        timestamp=past,
        ttl=1.0,
    )
    n = await bus.publish(msg)
    assert n == 1  # delivered despite expiration
    assert len(received) == 1


@pytest.mark.asyncio
async def test_default_ttl_applied():
    bus = AgentMessageBus(config=BusConfig(default_ttl=60.0))
    msg = AgentMessage.task_request("a", "b")  # ttl=0
    await bus.publish(msg)
    assert msg.ttl == 60.0  # default applied


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_messages_enabled():
    repo = InMemoryMessageRepository()
    bus = AgentMessageBus(repository=repo, config=BusConfig(persist_messages=True))
    await bus.publish(AgentMessage.task_request("a", "b"))
    assert repo.count() == 1


@pytest.mark.asyncio
async def test_persist_messages_disabled():
    repo = InMemoryMessageRepository()
    bus = AgentMessageBus(repository=repo, config=BusConfig(persist_messages=False))
    await bus.publish(AgentMessage.task_request("a", "b"))
    assert repo.count() == 0


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history():
    bus = AgentMessageBus()
    for i in range(5):
        await bus.publish(AgentMessage.task_request("a", "b", payload={"i": i}))
    history = bus.history()
    assert len(history) == 5
    # Newest last.
    assert history[-1].payload["i"] == 4


@pytest.mark.asyncio
async def test_history_by_correlation():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("a", "b", correlation_id="c1"))
    await bus.publish(AgentMessage.task_response("b", "a", correlation_id="c1"))
    await bus.publish(AgentMessage.task_request("a", "b", correlation_id="c2"))
    results = bus.history_by_correlation("c1")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_history_by_sender():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("alice", "b"))
    await bus.publish(AgentMessage.task_request("bob", "a"))
    await bus.publish(AgentMessage.task_request("alice", "c"))
    results = bus.history_by_sender("alice", limit=10)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_history_by_receiver():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("a", "bob"))
    await bus.publish(AgentMessage.task_request("b", "alice"))
    results = bus.history_by_receiver("bob", limit=10)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_get_message():
    bus = AgentMessageBus()
    msg = AgentMessage.task_request("a", "b")
    await bus.publish(msg)
    retrieved = bus.get_message(msg.message_id)
    assert retrieved is not None
    assert retrieved.sender_agent == "a"


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics():
    bus = AgentMessageBus()
    await bus.subscribe("coder", _noop_handler)
    await bus.publish(AgentMessage.task_request("a", "coder"))
    m = bus.metrics()
    assert m["published_total"] == 1
    assert m["delivered_total"] == 1
    assert m["by_type"]["task_request"] == 1
    assert m["subscriber_count"] == 1
    assert m["repository_count"] == 1


@pytest.mark.asyncio
async def test_metrics_no_subscriber():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("a", "coder"))
    m = bus.metrics()
    assert m["dropped_no_subscriber"] == 1


# ----------------------------------------------------------------------
# Clear
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("a", "b"))
    assert len(bus.history()) == 1
    await bus.clear()
    assert len(bus.history()) == 0


@pytest.mark.asyncio
async def test_clear_all():
    bus = AgentMessageBus()
    await bus.publish(AgentMessage.task_request("a", "b"))
    assert len(bus.history()) == 1
    assert bus.repository.count() == 1
    await bus.clear_all()
    assert len(bus.history()) == 0
    assert bus.repository.count() == 0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


async def _noop_handler(msg: AgentMessage) -> None:
    pass


# ----------------------------------------------------------------------
# Concurrent messaging
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_publish():
    """Multiple coroutines publishing concurrently should all succeed."""
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def handler(msg):
        received.append(msg)

    await bus.subscribe("coder", handler)

    async def publish_one(i: int):
        await bus.publish(AgentMessage.task_request("planner", "coder", payload={"i": i}))

    await asyncio.gather(*[publish_one(i) for i in range(20)])
    assert len(received) == 20
    # All messages should be persisted.
    assert bus.repository.count() == 20


@pytest.mark.asyncio
async def test_concurrent_subscribe_publish():
    """Subscribe and publish concurrently."""
    bus = AgentMessageBus()
    received: list[AgentMessage] = []

    async def make_handler(name: str):
        async def h(msg):
            received.append(msg)

        return h

    async def subscribe_one(name: str):
        await bus.subscribe(name, await make_handler(name))

    async def publish_one(name: str):
        await bus.publish(AgentMessage.task_request("planner", name))

    # Subscribe to 5 agents concurrently.
    await asyncio.gather(*[subscribe_one(f"agent-{i}") for i in range(5)])
    # Publish to all 5 concurrently.
    await asyncio.gather(*[publish_one(f"agent-{i}") for i in range(5)])
    assert len(received) == 5
