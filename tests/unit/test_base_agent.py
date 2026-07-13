"""Unit tests for BaseAgent lifecycle and safe_handle.

Sprint 1.5 - Phase 6.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
AGENTS_SRC = ROOT / "packages" / "agents"

sys.path.insert(0, str(CORE_SRC))
sys.path.insert(0, str(AGENTS_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.base import BaseAgent, AgentState  # noqa: E402


class SimpleAgent(BaseAgent):
    """Concrete agent for testing."""

    name = "simple"
    description = "A simple test agent"
    capabilities = ["test"]

    def __init__(self, *, fail: bool = False, delay: float = 0):
        super().__init__()
        self._fail = fail
        self._delay = delay
        self.handle_called = False

    async def handle(self, message: str, context: dict) -> str:
        self.handle_called = True
        if self._delay:
            import asyncio
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("intentional failure")
        return f"handled: {message}"


# --- Creation tests ---


def test_cannot_instantiate_abstract_base_agent() -> None:
    with pytest.raises(TypeError):
        BaseAgent()


def test_concrete_agent_created_state() -> None:
    agent = SimpleAgent()
    assert agent.state == AgentState.CREATED
    assert agent.is_ready is False


def test_agent_has_unique_id() -> None:
    a1 = SimpleAgent()
    a2 = SimpleAgent()
    assert a1.agent_id != a2.agent_id


def test_agent_metadata() -> None:
    agent = SimpleAgent()
    meta = agent.metadata
    assert meta["name"] == "simple"
    assert meta["state"] == "created"
    assert meta["capabilities"] == ["test"]
    assert meta["tasks_completed"] == 0


def test_agent_repr() -> None:
    agent = SimpleAgent()
    r = repr(agent)
    assert "simple" in r
    assert "created" in r


# --- Lifecycle tests ---


@pytest.mark.asyncio
async def test_initialize_transitions_to_ready() -> None:
    agent = SimpleAgent()
    await agent.initialize()
    assert agent.state == AgentState.READY
    assert agent.is_ready is True


@pytest.mark.asyncio
async def test_double_initialize_raises() -> None:
    agent = SimpleAgent()
    await agent.initialize()
    with pytest.raises(RuntimeError, match="cannot initialize"):
        await agent.initialize()


@pytest.mark.asyncio
async def test_shutdown_from_ready() -> None:
    agent = SimpleAgent()
    await agent.initialize()
    await agent.shutdown()
    assert agent.state == AgentState.SHUTDOWN


@pytest.mark.asyncio
async def test_shutdown_from_created() -> None:
    agent = SimpleAgent()
    await agent.shutdown()
    assert agent.state == AgentState.SHUTDOWN


@pytest.mark.asyncio
async def test_shutdown_from_failed() -> None:
    agent = SimpleAgent(fail=True)
    await agent.initialize()
    await agent.safe_handle("test", {})
    assert agent.state == AgentState.FAILED
    await agent.shutdown()
    assert agent.state == AgentState.SHUTDOWN


# --- Execution tests ---


@pytest.mark.asyncio
async def test_safe_handle_success() -> None:
    agent = SimpleAgent()
    await agent.initialize()
    result = await agent.safe_handle("hello", {"session_id": "s1"})

    assert result["content"] == "handled: hello"
    assert result["state"] == "completed"
    assert result["error"] is None
    assert result["duration_ms"] >= 0
    assert agent.tasks_completed == 1
    assert agent.handle_called is True


@pytest.mark.asyncio
async def test_safe_handle_failure() -> None:
    agent = SimpleAgent(fail=True)
    await agent.initialize()
    result = await agent.safe_handle("test", {})

    assert result["content"] == ""
    assert result["state"] == "failed"
    assert "intentional failure" in result["error"]
    assert agent.tasks_failed == 1


@pytest.mark.asyncio
async def test_safe_handle_not_ready_rejects() -> None:
    agent = SimpleAgent()
    # Not initialized — should reject.
    result = await agent.safe_handle("test", {})
    assert "not ready" in result["error"]


@pytest.mark.asyncio
async def test_safe_handle_after_shutdown_rejects() -> None:
    agent = SimpleAgent()
    await agent.initialize()
    await agent.shutdown()
    result = await agent.safe_handle("test", {})
    assert "not ready" in result["error"]


@pytest.mark.asyncio
async def test_metrics_accumulate() -> None:
    agent = SimpleAgent()
    await agent.initialize()

    for _ in range(3):
        await agent.safe_handle("ok", {})

    assert agent.tasks_completed == 3
    assert agent.tasks_failed == 0


@pytest.mark.asyncio
async def test_last_error_cleared_on_success() -> None:
    agent = SimpleAgent(fail=True)
    await agent.initialize()

    await agent.safe_handle("test", {})
    assert agent.last_error is not None

    agent._fail = False
    await agent.safe_handle("ok", {})
    assert agent.last_error is None


@pytest.mark.asyncio
async def test_completed_state_returns_to_ready_after_safe_handle() -> None:
    """After safe_handle, state should be COMPLETED, allowing another call."""
    agent = SimpleAgent()
    await agent.initialize()
    await agent.safe_handle("test", {})
    assert agent.state == AgentState.COMPLETED

    # Can still handle another message.
    result = await agent.safe_handle("test2", {})
    assert result["state"] == "completed"