"""Unit tests for communication.collaboration (Sprint 2.9)."""

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
    Collaboration,
    CollaborationManager,
    CollaborationStatus,
    CollaborationTimeoutError,
    MessageType,
)

# ----------------------------------------------------------------------
# Collaboration dataclass
# ----------------------------------------------------------------------


def test_collaboration_defaults():
    c = Collaboration(initiator="planner", participant="coder")
    assert c.id.startswith("collab-")
    assert c.status == CollaborationStatus.INITIATED
    assert c.messages == []
    assert c.result is None
    assert c.error is None
    assert c.completed_at is None


def test_collaboration_is_terminal():
    c = Collaboration(initiator="a", participant="b")
    assert not c.is_terminal
    c.complete()
    assert c.is_terminal


def test_collaboration_complete():
    c = Collaboration(initiator="a", participant="b")
    c.complete(result={"output": "done"})
    assert c.status == CollaborationStatus.COMPLETED
    assert c.result == {"output": "done"}
    assert c.completed_at is not None


def test_collaboration_fail():
    c = Collaboration(initiator="a", participant="b")
    c.fail("something broke")
    assert c.status == CollaborationStatus.FAILED
    assert c.error == "something broke"


def test_collaboration_cancel():
    c = Collaboration(initiator="a", participant="b")
    c.cancel(reason="user aborted")
    assert c.status == CollaborationStatus.CANCELLED
    assert c.error == "user aborted"


def test_collaboration_timeout():
    c = Collaboration(initiator="a", participant="b")
    c.timeout(what="response")
    assert c.status == CollaborationStatus.TIMED_OUT
    assert "response" in c.error


def test_collaboration_add_message_response():
    c = Collaboration(initiator="a", participant="b")
    msg = AgentMessage.task_response("b", "a")
    c.add_message(msg)
    assert c.status == CollaborationStatus.IN_PROGRESS


def test_collaboration_add_message_error():
    c = Collaboration(initiator="a", participant="b")
    msg = AgentMessage.error_report("b", "a", error="boom")
    c.add_message(msg)
    assert c.status == CollaborationStatus.FAILED
    assert c.error == "boom"


def test_collaboration_to_dict():
    c = Collaboration(initiator="a", participant="b", task_id="t1")
    c.complete(result={"x": 1})
    d = c.to_dict()
    assert d["initiator"] == "a"
    assert d["participant"] == "b"
    assert d["status"] == "completed"
    assert d["task_id"] == "t1"
    assert d["result"] == {"x": 1}


# ----------------------------------------------------------------------
# CollaborationManager — delegate
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_returns_collaboration():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def handler(msg):
        pass

    await bus.subscribe("coder", handler)
    collab = await mgr.delegate("planner", "coder", task_id="t1", payload={"x": 1})
    assert isinstance(collab, Collaboration)
    assert collab.initiator == "planner"
    assert collab.participant == "coder"
    assert collab.task_id == "t1"
    assert collab.initial_message is not None
    assert collab.initial_message.message_type == MessageType.TASK_REQUEST
    assert len(collab.messages) == 1


@pytest.mark.asyncio
async def test_delegate_with_response():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def coder_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(MessageType.TASK_RESPONSE, payload={"result": "done"})
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=2.0)
    assert response.message_type == MessageType.TASK_RESPONSE
    assert response.payload["result"] == "done"


# ----------------------------------------------------------------------
# request_help
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_help_returns_collaboration():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    collab = await mgr.request_help("planner", topic="need help with code")
    assert collab.initiator == "planner"
    assert collab.participant is None  # unknown until someone responds
    assert collab.initial_message.is_broadcast


@pytest.mark.asyncio
async def test_request_help_with_response():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def helper_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST and msg.is_broadcast:
            response = msg.reply(MessageType.TASK_RESPONSE, payload={"i_can_help": True})
            # For broadcast replies, sender_agent must be set explicitly.
            response.sender_agent = "helper-agent"
            await bus.publish(response)

    await bus.subscribe_broadcast(helper_handler)
    collab = await mgr.request_help("planner", topic="need help")
    response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=2.0)
    assert response.message_type == MessageType.TASK_RESPONSE
    assert response.payload["i_can_help"] is True
    # Participant should be set to the responder.
    assert collab.participant == "helper-agent"


# ----------------------------------------------------------------------
# transfer_context
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_context():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    received: list[AgentMessage] = []

    async def coder_handler(msg):
        received.append(msg)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.transfer_context(
        "planner",
        "coder",
        context={"files": ["a.py", "b.py"]},
        task_id="t1",
    )
    assert collab.status == CollaborationStatus.COMPLETED
    assert len(received) == 1
    assert received[0].message_type == MessageType.CONTEXT_TRANSFER
    assert received[0].payload["context"] == {"files": ["a.py", "b.py"]}


# ----------------------------------------------------------------------
# handoff
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handoff():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    received: list[AgentMessage] = []

    async def coder_handler(msg):
        received.append(msg)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.handoff(
        "planner",
        "coder",
        task_id="t1",
        reason="specialized",
    )
    assert collab.status == CollaborationStatus.INITIATED
    assert len(received) == 1
    assert received[0].message_type == MessageType.HANDOFF
    assert received[0].payload["reason"] == "specialized"


# ----------------------------------------------------------------------
# wait_for_response — timeout
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_response_timeout():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=0.5)

    # No subscriber -> no response.
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    with pytest.raises(CollaborationTimeoutError):
        await mgr.wait_for_response(collab.initial_message.message_id, timeout=0.5)
    assert collab.status == CollaborationStatus.TIMED_OUT


# ----------------------------------------------------------------------
# wait_for_completion
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_completion():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def coder_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(
                MessageType.TASK_RESPONSE,
                payload={"done": True, "result": "ok"},
            )
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    final = await mgr.wait_for_completion(collab.id, timeout=2.0)
    assert final.status == CollaborationStatus.COMPLETED


@pytest.mark.asyncio
async def test_wait_for_completion_timeout():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=0.3)

    # No subscriber.
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    with pytest.raises(CollaborationTimeoutError):
        await mgr.wait_for_completion(collab.id, timeout=0.3)


@pytest.mark.asyncio
async def test_wait_for_completion_unknown_id():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    with pytest.raises(KeyError):
        await mgr.wait_for_completion("nonexistent")


# ----------------------------------------------------------------------
# Cancel / complete
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    ok = mgr.cancel(collab.id, reason="user aborted")
    assert ok is True
    assert collab.status == CollaborationStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_unknown():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    assert mgr.cancel("nonexistent") is False


@pytest.mark.asyncio
async def test_complete_manually():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    ok = mgr.complete(collab.id, result={"output": "done"})
    assert ok is True
    assert collab.status == CollaborationStatus.COMPLETED
    assert collab.result == {"output": "done"}


# ----------------------------------------------------------------------
# Queries
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_collaboration():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    assert mgr.get_collaboration(collab.id) is collab
    assert mgr.get_collaboration("nonexistent") is None


@pytest.mark.asyncio
async def test_list_active():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    c1 = await mgr.delegate("planner", "coder1", payload={"x": 1})
    await mgr.delegate("planner", "coder2", payload={"x": 2})
    active = mgr.list_active()
    assert len(active) == 2
    mgr.complete(c1.id)
    active = mgr.list_active()
    assert len(active) == 1


@pytest.mark.asyncio
async def test_list_by_initiator():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    await mgr.delegate("planner", "coder1", payload={"x": 1})
    await mgr.delegate("planner", "coder2", payload={"x": 2})
    await mgr.delegate("coder1", "coder2", payload={"x": 3})
    results = mgr.list_by_initiator("planner")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_by_participant():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    await mgr.delegate("planner", "coder", payload={"x": 1})
    await mgr.delegate("planner", "coder", payload={"x": 2})
    await mgr.delegate("planner", "reviewer", payload={"x": 3})
    results = mgr.list_by_participant("coder")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_by_task():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    await mgr.delegate("planner", "coder", task_id="t1", payload={"x": 1})
    await mgr.delegate("planner", "coder", task_id="t1", payload={"x": 2})
    await mgr.delegate("planner", "coder", task_id="t2", payload={"x": 3})
    results = mgr.list_by_task("t1")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_history():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    for i in range(3):
        await mgr.delegate("planner", "coder", payload={"i": i})
    history = mgr.history()
    assert len(history) == 3


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_initial():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    m = mgr.metrics()
    assert m["started_total"] == 0
    assert m["active_count"] == 0


@pytest.mark.asyncio
async def test_metrics_after_delegate():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)
    await mgr.delegate("planner", "coder", payload={"x": 1})
    m = mgr.metrics()
    assert m["started_total"] == 1
    assert m["active_count"] == 1


@pytest.mark.asyncio
async def test_metrics_after_completion():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus)

    async def coder_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(MessageType.TASK_RESPONSE, payload={"done": True})
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    await mgr.wait_for_completion(collab.id, timeout=2.0)
    m = mgr.metrics()
    assert m["completed_total"] == 1


@pytest.mark.asyncio
async def test_metrics_after_timeout():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=0.3)
    await mgr.delegate("planner", "coder", payload={"x": 1})
    # Wait for the timeout to be recorded.
    await asyncio.sleep(0.5)
    # Force a wait_for_response to trigger the timeout.
    collab = mgr.list_active()[0]
    with pytest.raises(CollaborationTimeoutError):
        await mgr.wait_for_response(collab.initial_message.message_id, timeout=0.3)
    m = mgr.metrics()
    assert m["timed_out_total"] == 1


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_report_marks_failed():
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def coder_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST:
            error = msg.reply(MessageType.ERROR_REPORT, payload={"error": "I can't do this"})
            await bus.publish(error)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    # The error should arrive and mark the collab as failed.
    await asyncio.sleep(0.1)
    assert collab.status == CollaborationStatus.FAILED
    assert collab.error == "I can't do this"


# ----------------------------------------------------------------------
# Auto-routing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_route_message():
    """Responses are auto-routed without manual route_message call."""
    bus = AgentMessageBus()
    mgr = CollaborationManager(bus=bus, default_timeout=2.0)

    async def coder_handler(msg):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(MessageType.TASK_RESPONSE, payload={"result": "ok"})
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    # Without calling route_message manually, the response should
    # still arrive.
    response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=2.0)
    assert response.payload["result"] == "ok"
