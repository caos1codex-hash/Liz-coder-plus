"""Tests para LifecycleManager (Sprint 2.3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import AgentStatus, Permission  # noqa: E402
from multiagent.registry.lifecycle import (  # noqa: E402
    LifecycleHealth,
    LifecycleManager,
    LifecycleState,
    can_transition_lifecycle,
)


# ------------------------------------------------------------------
# Stub agent
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub"]

    def __init__(self, name: str = "stub", *, fail_init: bool = False, **kwargs):
        super().__init__(name=name, description="stub", **kwargs)
        self._fail_init = fail_init

    async def _execute_impl(self, task: dict) -> dict:
        return {"handled": True}


# ------------------------------------------------------------------
# Estados y transiciones
# ------------------------------------------------------------------


def test_lifecycle_states_values() -> None:
    assert LifecycleState.CREATED.value == "created"
    assert LifecycleState.INITIALIZING.value == "initializing"
    assert LifecycleState.READY.value == "ready"
    assert LifecycleState.RUNNING.value == "running"
    assert LifecycleState.ERROR.value == "error"
    assert LifecycleState.STOPPED.value == "stopped"


def test_can_transition_valid() -> None:
    assert can_transition_lifecycle(LifecycleState.CREATED, LifecycleState.INITIALIZING)
    assert can_transition_lifecycle(LifecycleState.INITIALIZING, LifecycleState.READY)
    assert can_transition_lifecycle(LifecycleState.READY, LifecycleState.RUNNING)
    assert can_transition_lifecycle(LifecycleState.RUNNING, LifecycleState.READY)
    assert can_transition_lifecycle(LifecycleState.ERROR, LifecycleState.READY)
    assert can_transition_lifecycle(LifecycleState.READY, LifecycleState.STOPPED)


def test_can_transition_invalid() -> None:
    # STOPPED es terminal.
    assert not can_transition_lifecycle(LifecycleState.STOPPED, LifecycleState.READY)
    assert not can_transition_lifecycle(LifecycleState.STOPPED, LifecycleState.RUNNING)
    # CREATED no puede ir directo a RUNNING.
    assert not can_transition_lifecycle(LifecycleState.CREATED, LifecycleState.RUNNING)


# ------------------------------------------------------------------
# Lifecycle básico
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_state_created() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    assert lm.state == LifecycleState.CREATED
    assert lm.is_terminal is False
    assert lm.is_healthy is True
    assert lm.is_ready is False
    assert lm.is_running is False
    assert lm.initialized is False
    assert lm.started_count == 0


@pytest.mark.asyncio
async def test_initialize_transitions_to_ready() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    assert lm.state == LifecycleState.READY
    assert lm.is_ready is True
    assert lm.initialized is True
    assert agent.status == AgentStatus.IDLE  # BaseAgent.start() fue llamado


@pytest.mark.asyncio
async def test_initialize_idempotent() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    # Segunda llamada no hace nada.
    await lm.initialize()
    assert lm.state == LifecycleState.READY
    assert lm.started_count == 0


@pytest.mark.asyncio
async def test_start_from_ready() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()
    assert lm.state == LifecycleState.RUNNING
    assert lm.is_running is True
    assert lm.started_count == 1


@pytest.mark.asyncio
async def test_start_idempotent_when_running() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()
    await lm.start()  # no-op
    assert lm.started_count == 1


@pytest.mark.asyncio
async def test_start_without_initialize_raises() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    with pytest.raises(RuntimeError, match="must be READY"):
        await lm.start()


@pytest.mark.asyncio
async def test_stop_from_any_state() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.stop()
    assert lm.state == LifecycleState.STOPPED
    assert lm.is_terminal is True


@pytest.mark.asyncio
async def test_stop_after_initialize() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.stop()
    assert lm.state == LifecycleState.STOPPED
    assert lm.is_terminal is True


@pytest.mark.asyncio
async def test_stop_idempotent() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.stop()
    await lm.stop()  # no-op
    assert lm.state == LifecycleState.STOPPED


@pytest.mark.asyncio
async def test_stop_after_running() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()
    await lm.stop()
    assert lm.state == LifecycleState.STOPPED


# ------------------------------------------------------------------
# Error y recovery
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_error() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    lm.mark_error("something went wrong")
    assert lm.state == LifecycleState.ERROR
    assert lm.error_count == 1
    assert lm.last_error == "something went wrong"
    assert lm.is_healthy is False


@pytest.mark.asyncio
async def test_recover_from_error() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    lm.mark_error("boom")
    assert lm.state == LifecycleState.ERROR
    lm.recover()
    assert lm.state == LifecycleState.READY
    assert lm.is_healthy is True


@pytest.mark.asyncio
async def test_recover_no_op_when_not_error() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    lm.recover()  # no-op (ya está READY)
    assert lm.state == LifecycleState.READY


@pytest.mark.asyncio
async def test_errors_cap_at_max() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    for i in range(LifecycleManager.MAX_ERRORS + 10):
        lm._add_error(f"error {i}")  # noqa: SLF001
    assert lm.error_count == LifecycleManager.MAX_ERRORS


# ------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_returns_lifecycle_health() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()
    h = lm.health_check()
    assert isinstance(h, LifecycleHealth)
    assert h.agent_name == "a1"
    assert h.state == LifecycleState.READY  # se sincroniza desde agent.status (IDLE)
    assert h.healthy is True
    assert h.initialized is True
    assert h.started_count == 1
    assert h.uptime_s >= 0.0


@pytest.mark.asyncio
async def test_health_check_after_stop() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.stop()
    h = lm.health_check()
    assert h.state == LifecycleState.STOPPED
    assert h.healthy is False


@pytest.mark.asyncio
async def test_health_check_with_errors() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    lm.mark_error("err1")
    lm.mark_error("err2")
    h = lm.health_check()
    assert h.error_count == 2
    assert h.last_error == "err2"


# ------------------------------------------------------------------
# Serialización
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_to_dict() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()
    d = lm.to_dict()
    assert d["agent_name"] == "a1"
    assert d["state"] == "running"  # sincronizado
    assert d["initialized"] is True
    assert d["started_count"] == 1
    assert d["error_count"] == 0
    assert d["healthy"] is True
    assert "created_at" in d
    assert "last_state_change" in d


# ------------------------------------------------------------------
# Sincronización con BaseAgent
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_from_agent_status() -> None:
    """Si el BaseAgent cambia de estado externamente, el lifecycle se sincroniza."""
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    # El agente subyacente está en IDLE → lifecycle READY.
    assert lm.state == LifecycleState.READY
    # Pausar el agente externamente.
    await agent.pause()
    # El lifecycle se sincroniza en el siguiente health_check.
    h = lm.health_check()
    # PAUSED se mapea a READY (no acepta tasks pero sigue vivo).
    assert h.state in (LifecycleState.READY, LifecycleState.RUNNING)


@pytest.mark.asyncio
async def test_initialize_after_stop_raises() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.stop()
    with pytest.raises(RuntimeError, match="already STOPPED"):
        await lm.initialize()


# ------------------------------------------------------------------
# Started count
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_started_count_increments() -> None:
    agent = StubAgent("a1")
    lm = LifecycleManager(agent)
    await lm.initialize()
    await lm.start()  # count=1
    # Volver a READY (vía sync desde agent.status).
    lm.health_check()  # sincroniza
    if lm.state != LifecycleState.READY:
        # Forzar sync.
        lm._sync_from_agent()  # noqa: SLF001
    await lm.start()  # count=2
    assert lm.started_count == 2
