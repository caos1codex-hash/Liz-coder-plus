"""Integration tests for the communication package with existing modules (Sprint 2.9).

These tests verify that the communication package integrates cleanly
with the existing Sprint 2.1-2.8 modules:

- ``multiagent.messages.MessageBus`` / ``Message``
- ``multiagent.registry.AgentRegistry`` / ``AgentManifest``
- ``multiagent.workflow.EventBus``
- ``memory.repositories.ExecutionRepository`` (stubbed)

The tests follow the existing project convention of sys.path
manipulation at the top of each test module.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMM_SRC = ROOT / "packages" / "communication" / "src"
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(COMM_SRC))
sys.path.insert(0, str(MULTIAGENT_SRC))

for _k in [
    k for k in list(sys.modules) if k.startswith("communication") or k.startswith("multiagent")
]:
    sys.modules.pop(_k, None)

from communication import (  # noqa: E402
    AgentMessage,
    MessageType,
    build_integrated_bus,
    build_integrated_manager,
)
from communication.integration import (  # noqa: E402
    OrchestratorAdapter,
    RegistryAdapter,
)

# ----------------------------------------------------------------------
# Stub ExecutionRepository
# ----------------------------------------------------------------------


class StubExecutionRepository:
    """Stub that mimics ExecutionRepository's interface."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._next_id = 1

    def record(
        self,
        *,
        event_type,
        task_id=None,
        workflow_id=None,
        agent_name="",
        tool_name="",
        duration_ms=None,
        status="",
        error=None,
        result=None,
        metadata=None,
    ) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows.append(
            {
                "id": row_id,
                "event_type": event_type,
                "task_id": task_id,
                "workflow_id": workflow_id,
                "agent_name": agent_name,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "status": status,
                "error": error,
                "result": result,
                "metadata": metadata or {},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        return row_id

    def get_recent(self, limit=50):
        return list(reversed(self.rows))[:limit]

    def get_by_event_type(self, event_type, limit=100):
        return [r for r in self.rows if r["event_type"] == event_type][:limit]

    def get_by_task(self, task_id, limit=100):
        return [r for r in self.rows if r["task_id"] == task_id][:limit]

    def count_by_event_type(self):
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r["event_type"]] = counts.get(r["event_type"], 0) + 1
        return counts


# ----------------------------------------------------------------------
# Wire-format bridge
# ----------------------------------------------------------------------


def test_agent_message_to_from_wire():
    """AgentMessage <-> multiagent.Message round-trip preserves all fields."""
    from multiagent.messages import Message

    original = AgentMessage.task_request(
        "planner",
        "coder",
        task_id="t1",
        payload={"action": "write", "count": 42},
        correlation_id="corr-1",
        priority=MessageType.TASK_REQUEST and __import__("communication").MessagePriority.HIGH,
        ttl=30.0,
    )
    wire = original.to_message()
    assert isinstance(wire, Message)
    assert wire.sender == "planner"
    assert wire.receiver == "coder"
    assert wire.correlation_id == "corr-1"

    restored = AgentMessage.from_message(wire)
    assert restored.sender_agent == "planner"
    assert restored.receiver_agent == "coder"
    assert restored.task_id == "t1"
    assert restored.message_type == MessageType.TASK_REQUEST
    assert restored.correlation_id == "corr-1"
    assert restored.payload["action"] == "write"
    assert restored.payload["count"] == 42


# ----------------------------------------------------------------------
# build_integrated_bus with real MessageBus
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integrated_bus_with_real_message_bus():
    """The AgentMessageBus should work when wrapping a real MessageBus."""
    from multiagent.messages import MessageBus

    wire_bus = MessageBus()
    bus = build_integrated_bus(message_bus=wire_bus)

    received: list[AgentMessage] = []

    async def handler(msg: AgentMessage):
        received.append(msg)

    await bus.subscribe("coder", handler)
    await bus.publish(AgentMessage.task_request("planner", "coder", payload={"x": 1}))
    assert len(received) == 1
    # Persistence should work.
    assert bus.repository.count() == 1


@pytest.mark.asyncio
async def test_integrated_bus_with_execution_repository():
    """Persistence should flow through to the ExecutionRepository."""
    from multiagent.messages import MessageBus

    stub_repo = StubExecutionRepository()
    bus = build_integrated_bus(
        message_bus=MessageBus(),
        execution_repository=stub_repo,
    )
    await bus.publish(AgentMessage.task_request("planner", "coder", payload={"x": 1}))
    # The stub repo should have one row with event_type="agent.message".
    counts = stub_repo.count_by_event_type()
    assert counts.get("agent.message") == 1


@pytest.mark.asyncio
async def test_integrated_bus_with_event_bus_bridge():
    """When bridge_to_event_bus is enabled, messages should flow to the EventBus."""
    from multiagent.messages import MessageBus
    from multiagent.workflow.event_bus import EventBus

    wf_event_bus = EventBus()
    bus = build_integrated_bus(
        message_bus=MessageBus(),
        event_bus=wf_event_bus,
    )
    # Subscribe to the workflow EventBus.
    received_events: list = []

    async def event_handler(event):
        received_events.append(event)

    await wf_event_bus.subscribe(event_handler, event_type="agent.message.task_request")
    await bus.publish(AgentMessage.task_request("planner", "coder"))
    assert len(received_events) == 1


# ----------------------------------------------------------------------
# build_integrated_manager
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integrated_manager_end_to_end():
    """Full flow: delegate -> handler responds -> wait_for_response."""
    from multiagent.messages import MessageBus

    bus, mgr = build_integrated_manager(
        message_bus=MessageBus(),
        default_timeout=2.0,
    )

    async def coder_handler(msg: AgentMessage):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(
                MessageType.TASK_RESPONSE,
                payload={"result": "done", "done": True},
            )
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", task_id="t1", payload={"x": 1})
    response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=2.0)
    assert response.payload["result"] == "done"
    assert bus.repository.count() == 2  # request + response persisted


# ----------------------------------------------------------------------
# RegistryAdapter
# ----------------------------------------------------------------------


def _make_stub_registry():
    """Build a real AgentRegistry populated with standard manifests."""
    from multiagent.base import BaseAgent
    from multiagent.registry import AgentRegistry, standard_manifests

    class StubAgent(BaseAgent):
        async def _execute_impl(self, task):
            return {"ok": True}

    async def _build():
        reg = AgentRegistry()
        for manifest in standard_manifests():
            agent = StubAgent(name=manifest.id)
            await agent.start()
            await reg.register(
                agent,
                provided_capabilities=list(manifest.capabilities),
                priority=manifest.priority,
                metadata=dict(manifest.metadata),
            )
        return reg

    return asyncio.run(_build())


def test_registry_adapter_lists_agents():
    reg = _make_stub_registry()
    adapter = RegistryAdapter(reg)
    agents = adapter.list_agents()
    assert len(agents) == 7  # 7 standard manifests


def test_registry_adapter_get_agent():
    reg = _make_stub_registry()
    adapter = RegistryAdapter(reg)
    a = adapter.get_agent("coder")
    assert a is not None


def test_registry_adapter_agent_exists():
    reg = _make_stub_registry()
    adapter = RegistryAdapter(reg)
    assert adapter.agent_exists("coder") is True
    assert adapter.agent_exists("nonexistent") is False


def test_registry_adapter_agent_names():
    reg = _make_stub_registry()
    adapter = RegistryAdapter(reg)
    names = adapter.agent_names()
    assert "coder" in names
    assert "planner" in names


# ----------------------------------------------------------------------
# OrchestratorAdapter
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_adapter_delegate():
    """OrchestratorAdapter.delegate should use the CollaborationManager."""
    bus, mgr = build_integrated_manager(default_timeout=2.0)

    async def coder_handler(msg: AgentMessage):
        if msg.message_type == MessageType.TASK_REQUEST:
            response = msg.reply(MessageType.TASK_RESPONSE, payload={"done": True})
            await bus.publish(response)

    await bus.subscribe("coder", coder_handler)
    adapter = OrchestratorAdapter(orchestrator=None, manager=mgr)
    collab = await adapter.delegate(
        "planner",
        "coder",
        task={"action": "write"},
        task_id="t1",
    )
    assert collab is not None
    response = await adapter.wait_for_response(collab.initial_message.message_id, timeout=2.0)
    assert response.payload["done"] is True


@pytest.mark.asyncio
async def test_orchestrator_adapter_handoff():
    bus, mgr = build_integrated_manager(default_timeout=2.0)

    received: list[AgentMessage] = []

    async def coder_handler(msg: AgentMessage):
        received.append(msg)

    await bus.subscribe("coder", coder_handler)
    adapter = OrchestratorAdapter(orchestrator=None, manager=mgr)
    await adapter.handoff("planner", "coder", task_id="t1", reason="specialized")
    assert len(received) == 1
    assert received[0].message_type == MessageType.HANDOFF


# ----------------------------------------------------------------------
# Concurrent messaging with real bus
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_messaging_with_real_bus():
    """Multiple concurrent collaborations should all complete correctly."""
    from multiagent.messages import MessageBus

    bus, mgr = build_integrated_manager(message_bus=MessageBus(), default_timeout=5.0)

    # Each agent responds to TASK_REQUEST with a TASK_RESPONSE.
    async def make_handler(name: str):
        async def h(msg: AgentMessage):
            if msg.message_type == MessageType.TASK_REQUEST:
                response = msg.reply(MessageType.TASK_RESPONSE, payload={"responder": name})
                await bus.publish(response)

        return h

    for name in ["coder", "reviewer", "research"]:
        await bus.subscribe(name, await make_handler(name))

    # Delegate to all three concurrently.
    async def delegate_one(name: str):
        return await mgr.delegate("planner", name, payload={"x": 1})

    collabs = await asyncio.gather(*[delegate_one(n) for n in ["coder", "reviewer", "research"]])

    # Wait for all responses.
    async def wait_one(collab):
        return await mgr.wait_for_response(collab.initial_message.message_id, timeout=5.0)

    responses = await asyncio.gather(*[wait_one(c) for c in collabs])
    assert len(responses) == 3
    responders = {r.payload["responder"] for r in responses}
    assert responders == {"coder", "reviewer", "research"}


# ----------------------------------------------------------------------
# Failure handling with real bus
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_propagation_with_real_bus():
    """An ERROR_REPORT response should mark the collaboration as failed."""
    from multiagent.messages import MessageBus

    bus, mgr = build_integrated_manager(message_bus=MessageBus(), default_timeout=2.0)

    async def coder_handler(msg: AgentMessage):
        if msg.message_type == MessageType.TASK_REQUEST:
            error = msg.reply(MessageType.ERROR_REPORT, payload={"error": "I failed"})
            await bus.publish(error)

    await bus.subscribe("coder", coder_handler)
    collab = await mgr.delegate("planner", "coder", payload={"x": 1})
    # Give the error a moment to arrive.
    await asyncio.sleep(0.2)
    assert collab.status.value == "failed"
    assert collab.error == "I failed"


# ----------------------------------------------------------------------
# Existing tests still pass
# ----------------------------------------------------------------------


def test_existing_message_bus_still_works():
    """The existing MessageBus API must still be usable directly."""
    from multiagent.messages import MessageBus, Message
    from multiagent.enums import MessageType as WireType, Priority

    async def main():
        bus = MessageBus()
        received = []

        async def handler(msg):
            received.append(msg)

        await bus.subscribe("coder", handler)
        msg = Message(
            sender="planner",
            receiver="coder",
            type=WireType.REQUEST,
            payload={"x": 1},
            priority=Priority.NORMAL,
        )
        await bus.publish(msg)
        assert len(received) == 1

    asyncio.run(main())


def test_existing_workflow_event_bus_still_works():
    """The existing workflow EventBus must still be usable directly."""
    from multiagent.workflow.event_bus import EventBus

    async def main():
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        await bus.subscribe(handler, event_type="test.event")
        await bus.publish("test.event", source="test", payload={"x": 1})
        assert len(received) == 1

    asyncio.run(main())
