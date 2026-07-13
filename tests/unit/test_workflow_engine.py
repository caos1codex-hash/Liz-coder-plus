"""Tests para WorkflowEngine: failover, parallel execution, context (Sprint 2.2)."""

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

from multiagent.base import BaseAgent  # noqa: E402
from multiagent.enums import Permission  # noqa: E402
from multiagent.workflow.context import ContextManager  # noqa: E402
from multiagent.workflow.engine import (  # noqa: E402
    FailoverPolicy,
    WorkflowEngine,
)
from multiagent.workflow.enums import (  # noqa: E402
    StepCriticality,
    WorkflowStatus,
)
from multiagent.workflow.event_bus import EventBus  # noqa: E402
from multiagent.workflow.models import Step, Workflow  # noqa: E402


# ------------------------------------------------------------------
# Stub agents
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    """Agente stub configurable para tests."""

    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test"]

    def __init__(
        self,
        name: str = "stub",
        *,
        fail: bool = False,
        fail_n_times: int = 0,
        delay: float = 0.0,
        **kwargs,
    ):
        super().__init__(name=name, description="stub", **kwargs)
        self._fail = fail
        self._fail_n_times = fail_n_times  # falla las primeras N llamadas
        self._calls = 0
        self._delay = delay

    async def _execute_impl(self, task: dict) -> dict:
        self._calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError("always fails")
        # fallar las primeras N llamadas (determinista, sin decrementar).
        if self._calls <= self._fail_n_times:
            raise RuntimeError(f"fail #{self._calls}")
        return {"value": task.get("name"), "tools_used": []}


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


def _make_workflow(*steps: Step, name: str = "test_wf") -> Workflow:
    wf = Workflow(name=name)
    for s in steps:
        wf.add_step(s)
    return wf


# ------------------------------------------------------------------
# WorkflowEngine — ejecución básica
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_runs_simple_workflow() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.steps_completed == 1
    assert result.steps_failed == 0


@pytest.mark.asyncio
async def test_engine_runs_linear_dag() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a1", action="test", depends_on=[s1.id])
    s3 = Step(name="s3", agent="a1", action="test", depends_on=[s2.id])
    wf = _make_workflow(s1, s2, s3)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.steps_completed == 3


@pytest.mark.asyncio
async def test_engine_runs_diamond_dag() -> None:
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    engine = WorkflowEngine(agents=[a1, a2])
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test", depends_on=[s1.id])
    s3 = Step(name="s3", agent="a1", action="test", depends_on=[s1.id])
    s4 = Step(name="s4", agent="a2", action="test", depends_on=[s2.id, s3.id])
    wf = _make_workflow(s1, s2, s3, s4)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.steps_completed == 4
    # s2 y s3 pudieron correr en paralelo (dependen sólo de s1).
    assert result.parallelism_max >= 2


# ------------------------------------------------------------------
# Failover
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failover_retry_then_succeed() -> None:
    """Un step que falla 2 veces y luego tiene éxito: retry_count=2."""
    a1 = await _started(StubAgent("a1", fail_n_times=2))
    engine = WorkflowEngine(
        agents=[a1],
        failover=FailoverPolicy(max_retries=3, reassign_on_fail=False,
                                 cancel_on_required_fail=False),
    )
    s1 = Step(name="s1", agent="a1", action="test", max_retries=3)
    wf = _make_workflow(s1)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.retry_count == 2
    assert result.steps_completed == 1


@pytest.mark.asyncio
async def test_failover_reassign_to_another_agent() -> None:
    """Si un agente falla y reassign_on_fail=True, prueba con otro."""
    a1 = await _started(StubAgent("a1", fail=True))
    a2 = await _started(StubAgent("a2"))  # este sí funciona
    engine = WorkflowEngine(
        agents=[a1, a2],
        failover=FailoverPolicy(max_retries=0, reassign_on_fail=True,
                                 max_reassigns=2, cancel_on_required_fail=False),
    )
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.reassign_count >= 1


@pytest.mark.asyncio
async def test_failover_skip_optional_step() -> None:
    """Un step OPTIONAL fallido se SKIP y el workflow continúa."""
    a1 = await _started(StubAgent("a1", fail=True))
    a2 = await _started(StubAgent("a2"))
    engine = WorkflowEngine(
        agents=[a1, a2],
        failover=FailoverPolicy(max_retries=0, reassign_on_fail=False,
                                 skip_optional_on_fail=True,
                                 cancel_on_required_fail=True),
    )
    s1 = Step(name="s1", agent="a1", action="test",
              criticality=StepCriticality.OPTIONAL)
    s2 = Step(name="s2", agent="a2", action="test", depends_on=[s1.id])
    wf = _make_workflow(s1, s2)
    result = await engine.run(wf)
    # s1 fallido opcional → SKIP; s2 puede continuar (o también skip si
    # depende estrictamente). Como depende de s1 que está SKIP, s2 también
    # puede saltarse. El workflow debe completarse.
    assert result.status == WorkflowStatus.COMPLETED
    assert result.steps_skipped >= 1


@pytest.mark.asyncio
async def test_failover_cancel_on_required_failure() -> None:
    """Si un step REQUIRED falla definitivamente, el workflow se cancela."""
    a1 = await _started(StubAgent("a1", fail=True))
    engine = WorkflowEngine(
        agents=[a1],
        failover=FailoverPolicy(max_retries=0, reassign_on_fail=False,
                                 cancel_on_required_fail=True),
    )
    s1 = Step(name="s1", agent="a1", action="test",
              criticality=StepCriticality.REQUIRED)
    wf = _make_workflow(s1)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.FAILED
    assert result.steps_failed == 1


# ------------------------------------------------------------------
# Parallel execution
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_execution_with_delay() -> None:
    """4 steps independientes con delay se ejecutan en paralelo."""
    a1 = await _started(StubAgent("a1", delay=0.05))
    a2 = await _started(StubAgent("a2", delay=0.05))
    a3 = await _started(StubAgent("a3", delay=0.05))
    a4 = await _started(StubAgent("a4", delay=0.05))
    from multiagent.workflow.scheduler import SchedulerConfig
    engine = WorkflowEngine(
        agents=[a1, a2, a3, a4],
        scheduler_config=SchedulerConfig(max_parallel_steps=4, max_parallel_agents=4),
    )
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test")
    s3 = Step(name="s3", agent="a3", action="test")
    s4 = Step(name="s4", agent="a4", action="test")
    wf = _make_workflow(s1, s2, s3, s4)
    result = await engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.parallelism_max == 4
    # Si fueran secuenciales: 4 * 0.05s = 200ms. En paralelo: ~50ms.
    assert result.duration_ms < 200.0


@pytest.mark.asyncio
async def test_parallelism_limited_by_config() -> None:
    """Con max_parallel_steps=2, sólo 2 steps corren a la vez."""
    a1 = await _started(StubAgent("a1", delay=0.02))
    a2 = await _started(StubAgent("a2", delay=0.02))
    a3 = await _started(StubAgent("a3", delay=0.02))
    a4 = await _started(StubAgent("a4", delay=0.02))
    from multiagent.workflow.scheduler import SchedulerConfig
    engine = WorkflowEngine(
        agents=[a1, a2, a3, a4],
        scheduler_config=SchedulerConfig(max_parallel_steps=2, max_parallel_agents=4),
    )
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test")
    s3 = Step(name="s3", agent="a3", action="test")
    s4 = Step(name="s4", agent="a4", action="test")
    wf = _make_workflow(s1, s2, s3, s4)
    result = await engine.run(wf)
    assert result.parallelism_max == 2


# ------------------------------------------------------------------
# Context Manager
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_objectives() -> None:
    ctx = ContextManager("wf-1")
    await ctx.add_objective("refactor module")
    await ctx.add_objective("add tests")
    assert ctx.objectives() == ["refactor module", "add tests"]
    removed = await ctx.remove_objective(0)
    assert removed == "refactor module"
    assert ctx.objectives() == ["add tests"]


@pytest.mark.asyncio
async def test_context_files() -> None:
    ctx = ContextManager("wf-1")
    snap = await ctx.register_file("a.py", "content here", modified_by="coder")
    assert snap.size > 0
    assert len(snap.content_hash) == 64  # SHA-256
    fetched = ctx.get_file("a.py")
    assert fetched is not None
    assert fetched.content == "content here"


@pytest.mark.asyncio
async def test_context_snippets() -> None:
    ctx = ContextManager("wf-1")
    snippet = await ctx.add_snippet("greet", "def greet(): pass", created_by="coder")
    fetched = ctx.get_snippet(snippet.id)
    assert fetched is not None
    assert fetched.name == "greet"


@pytest.mark.asyncio
async def test_context_variables() -> None:
    ctx = ContextManager("wf-1")
    await ctx.set_variable("count", 42)
    assert ctx.get_variable("count") == 42
    assert ctx.get_variable("missing", "default") == "default"
    await ctx.remove_variable("count")
    assert ctx.get_variable("count") is None


@pytest.mark.asyncio
async def test_context_messages() -> None:
    ctx = ContextManager("wf-1")
    await ctx.add_message("a1", "a2", "hello")
    msgs = ctx.messages()
    assert len(msgs) == 1
    assert msgs[0].content == "hello"


@pytest.mark.asyncio
async def test_context_share_memory() -> None:
    ctx = ContextManager("wf-1")
    await ctx.share_memory("a1", "key1", {"value": 42})
    assert ctx.get_shared_memory("a1") == {"key1": {"value": 42}}


@pytest.mark.asyncio
async def test_context_transfer() -> None:
    ctx = ContextManager("wf-1")
    await ctx.set_variable("k", "v")
    await ctx.share_memory("a1", "mem1", "mem_value")
    await ctx.add_snippet("s1", "code", created_by="a1")
    transferred = await ctx.transfer_context("a1", "a2")
    assert transferred["variables"] == {"k": "v"}
    assert transferred["memory"] == {"mem1": "mem_value"}
    assert len(transferred["snippets"]) == 1
    # a2 ahora tiene la memoria de a1.
    assert ctx.get_shared_memory("a2") == {"mem1": "mem_value"}


@pytest.mark.asyncio
async def test_context_snapshot() -> None:
    ctx = ContextManager("wf-1")
    await ctx.add_objective("obj1")
    await ctx.set_variable("k", "v")
    snap = ctx.snapshot()
    assert snap["workflow_id"] == "wf-1"
    assert snap["objectives"] == ["obj1"]
    assert snap["variables"] == {"k": "v"}


# ------------------------------------------------------------------
# WorkflowEngine — pause / resume / cancel
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_cancel_workflow() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    await engine.submit(wf)
    ok = await engine.cancel(wf.id, reason="test")
    assert ok is True
    assert wf.status == WorkflowStatus.CANCELLED


@pytest.mark.asyncio
async def test_engine_pause_and_resume() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    await engine.submit(wf)
    wf.transition_to(WorkflowStatus.RUNNING)
    ok = await engine.pause(wf.id)
    assert ok is True
    assert wf.status == WorkflowStatus.PAUSED
    ok = await engine.resume(wf.id)
    assert ok is True
    assert wf.status == WorkflowStatus.RUNNING


@pytest.mark.asyncio
async def test_engine_invalid_dag_raises_on_submit() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1")
    s2 = Step(name="s2", depends_on=[s1.id])
    wf = Workflow(name="bad")
    wf.add_step(s1)
    wf.add_step(s2)
    # Crear ciclo.
    wf.add_dependency(s1.id, s2.id)
    with pytest.raises(ValueError, match="invalid DAG"):
        await engine.submit(wf)


# ------------------------------------------------------------------
# Eventos
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_emits_workflow_lifecycle_events() -> None:
    a1 = await _started(StubAgent("a1"))
    bus = EventBus()
    received: list[str] = []

    async def handler(e):
        received.append(e.type_str)

    await bus.subscribe(handler)
    engine = WorkflowEngine(agents=[a1], event_bus=bus)
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    await engine.run(wf)
    assert "workflow.created" in received
    assert "workflow.started" in received
    assert "workflow.completed" in received
    assert "step.started" in received
    assert "step.finished" in received


@pytest.mark.asyncio
async def test_engine_emits_failover_events_on_retry() -> None:
    a1 = await _started(StubAgent("a1", fail_n_times=1))
    bus = EventBus()
    received: list[str] = []

    async def handler(e):
        received.append(e.type_str)

    await bus.subscribe(handler)
    engine = WorkflowEngine(
        agents=[a1], event_bus=bus,
        failover=FailoverPolicy(max_retries=2, reassign_on_fail=False,
                                 cancel_on_required_fail=False),
    )
    s1 = Step(name="s1", agent="a1", action="test", max_retries=2)
    wf = _make_workflow(s1)
    await engine.run(wf)
    assert "step.retry" in received
    assert "workflow.completed" in received


# ------------------------------------------------------------------
# Métricas del engine
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_metrics() -> None:
    a1 = await _started(StubAgent("a1"))
    engine = WorkflowEngine(agents=[a1])
    s1 = Step(name="s1", agent="a1", action="test")
    wf = _make_workflow(s1)
    await engine.run(wf)
    m = engine.metrics()
    assert "scheduler" in m
    assert "load_balancer" in m
    assert "event_bus" in m
    assert "logger" in m
    assert m["running_workflows"] == 1
    assert m["registered_agents"] == 1
