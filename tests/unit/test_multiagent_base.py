"""Tests para BaseAgent (Sprint 2.1)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.base import BaseAgent, HealthReport  # noqa: E402
from multiagent.enums import AgentStatus, HealthState, Permission  # noqa: E402


# ------------------------------------------------------------------
# Agente concreto de test
# ------------------------------------------------------------------


class EchoAgent(BaseAgent):
    """Agente de prueba que hace echo del payload."""

    default_permissions = frozenset({Permission.MEMORY_READ, Permission.MEMORY_WRITE})
    default_objectives = ["echo", "test"]
    default_tools = ["echo_tool"]

    def __init__(self, *, fail: bool = False, delay: float = 0.0, **kwargs):
        super().__init__(name="echo", description="Test echo agent", **kwargs)
        self._fail = fail
        self._delay = delay

    async def _execute_impl(self, task: dict) -> dict:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("intentional failure")
        return {
            "echo": task.get("payload", {}),
            "tools_used": ["echo_tool"],
            "tokens_prompt": 10,
            "tokens_completion": 20,
        }


# ------------------------------------------------------------------
# Instanciación
# ------------------------------------------------------------------


def test_cannot_instantiate_abstract() -> None:
    with pytest.raises(TypeError):
        BaseAgent("x")


def test_concrete_creation() -> None:
    a = EchoAgent()
    assert a.name == "echo"
    assert a.status == AgentStatus.CREATED
    assert a.description == "Test echo agent"
    assert Permission.MEMORY_READ in a.permissions
    assert "echo" in a.objectives
    assert a.id  # auto-gen
    assert a.uptime_s >= 0


def test_agent_unique_ids() -> None:
    a1 = EchoAgent()
    a2 = EchoAgent()
    assert a1.id != a2.id


def test_repr() -> None:
    a = EchoAgent()
    r = repr(a)
    assert "echo" in r
    assert "created" in r


# ------------------------------------------------------------------
# Permisos
# ------------------------------------------------------------------


def test_has_permission() -> None:
    a = EchoAgent()
    assert a.has_permission(Permission.MEMORY_READ)
    assert a.has_permission(Permission.MEMORY_WRITE)
    assert not a.has_permission(Permission.GIT_OPERATIONS)


def test_require_permission_raises() -> None:
    a = EchoAgent()
    with pytest.raises(PermissionError, match="lacks permission"):
        a.require_permission(Permission.GIT_OPERATIONS)


def test_unrestricted_grants_all() -> None:
    a = EchoAgent(permissions=frozenset({Permission.UNRESTRICTED}))
    assert a.has_permission(Permission.GIT_OPERATIONS)
    assert a.has_permission(Permission.EXECUTE_COMMANDS)


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_transitions_to_idle() -> None:
    a = EchoAgent()
    await a.start()
    assert a.status == AgentStatus.IDLE


@pytest.mark.asyncio
async def test_start_from_non_created_raises() -> None:
    a = EchoAgent()
    await a.start()
    with pytest.raises(RuntimeError, match="can only start from CREATED"):
        await a.start()


@pytest.mark.asyncio
async def test_pause_resume() -> None:
    a = EchoAgent()
    await a.start()
    await a.pause()
    assert a.status == AgentStatus.PAUSED
    assert a.is_paused is True
    await a.resume()
    assert a.status == AgentStatus.IDLE
    assert a.is_paused is False


@pytest.mark.asyncio
async def test_stop() -> None:
    a = EchoAgent()
    await a.start()
    await a.stop()
    assert a.status == AgentStatus.STOPPED
    assert a.is_stopped


# ------------------------------------------------------------------
# Execute
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_success() -> None:
    a = EchoAgent()
    await a.start()
    result = await a.execute({"id": "t1", "name": "echo", "payload": {"x": 1}})
    assert result["status"] == "ok"
    assert result["result"]["echo"] == {"x": 1}
    assert result["agent"] == "echo"
    assert a.tasks_total == 1
    # El logger debe tener al menos 2 entradas (start + end).
    assert len(a.logger.history()) >= 2


@pytest.mark.asyncio
async def test_execute_failure() -> None:
    a = EchoAgent(fail=True)
    await a.start()
    result = await a.execute({"id": "t1", "name": "boom"})
    assert result["status"] == "error"
    assert "intentional failure" in result["error"]
    assert a.status == AgentStatus.ERROR
    assert a.tasks_failed == 1
    assert a.last_error is not None


@pytest.mark.asyncio
async def test_execute_after_stop_returns_error() -> None:
    a = EchoAgent()
    await a.start()
    await a.stop()
    result = await a.execute({"id": "t1", "name": "x"})
    assert result["status"] == "error"
    assert "stopped" in result["error"]


@pytest.mark.asyncio
async def test_execute_when_paused_waits_for_resume() -> None:
    a = EchoAgent()
    await a.start()
    await a.pause()

    async def _resume_after_delay() -> None:
        await asyncio.sleep(0.05)
        await a.resume()

    asyncio.create_task(_resume_after_delay())
    result = await a.execute({"id": "t1", "name": "x", "payload": {}})
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_execute_metrics_accumulate() -> None:
    a = EchoAgent()
    await a.start()
    for _ in range(3):
        await a.execute({"id": "t", "name": "x"})
    assert a.tasks_total == 3
    assert a._tasks_ok == 3  # noqa: SLF001


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_idle() -> None:
    a = EchoAgent()
    await a.start()
    h = a.health()
    assert isinstance(h, HealthReport)
    assert h.agent == "echo"
    assert h.state == HealthState.ALIVE
    assert h.status == AgentStatus.IDLE
    assert h.tasks_total == 0


@pytest.mark.asyncio
async def test_health_error_state() -> None:
    a = EchoAgent(fail=True)
    await a.start()
    await a.execute({"id": "t1", "name": "x"})
    h = a.health()
    assert h.state == HealthState.ERROR
    assert h.tasks_failed == 1


@pytest.mark.asyncio
async def test_health_dead_after_stop() -> None:
    a = EchoAgent()
    await a.start()
    await a.stop()
    h = a.health()
    assert h.state == HealthState.DEAD


# ------------------------------------------------------------------
# Serialize
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serialize() -> None:
    a = EchoAgent()
    await a.start()
    await a.execute({"id": "t1", "name": "x", "payload": {"k": 1}})
    d = a.serialize()
    assert d["name"] == "echo"
    assert d["status"] == "idle"
    assert d["tasks_total"] == 1
    assert "memory_read" in d["permissions"]
    assert "memory" in d
    assert "health" in d


# ------------------------------------------------------------------
# Memoria
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_short_after_execute() -> None:
    a = EchoAgent()
    await a.start()
    await a.execute({"id": "t1", "name": "x", "payload": {}})
    # El logger añade eventos a su propio buffer, no a la memoria del
    # agente. Verificamos que la memoria larga tiene la entrada store
    # que hace el agente de prueba (no, este echo no storea). Verificamos
    # que la memoria corta tiene el evento paused/etc al menos de lifecycle.
    # Aceptamos que haya al menos un evento de lifecycle.
    assert len(a.memory.recent(10)) >= 1


def test_memory_long_store_retrieve() -> None:
    a = EchoAgent()
    a.memory.store("k1", "v1", tags=["t"])
    entry = a.memory.retrieve("k1")
    assert entry is not None
    assert entry.value == "v1"
    assert "t" in entry.tags


# ------------------------------------------------------------------
# Mensajería
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_default_stores_in_short_memory() -> None:
    from multiagent.messages import Message

    a = EchoAgent()
    msg = Message.request("orchestrator", "echo", {"hello": "world"})
    await a.on_message(msg)
    recent = a.memory.recent(5)
    assert len(recent) >= 1
    last = recent[-1]
    assert last.value["from"] == "orchestrator"
    assert last.value["payload"] == {"hello": "world"}
