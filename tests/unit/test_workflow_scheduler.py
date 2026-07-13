"""Tests para LoadBalancer y Scheduler (Sprint 2.2)."""

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
from multiagent.enums import Permission  # noqa: E402
from multiagent.workflow.load_balancer import (  # noqa: E402
    LoadBalancer,
    LoadBalancerWeights,
)
from multiagent.workflow.scheduler import Scheduler, SchedulerConfig  # noqa: E402


# ------------------------------------------------------------------
# Stub agents
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test", "code"]

    def __init__(self, name: str = "stub", **kwargs):
        super().__init__(name=name, description="stub", **kwargs)

    async def _execute_impl(self, task: dict) -> dict:
        return {"handled": True}


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


# ------------------------------------------------------------------
# LoadBalancer
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_balancer_selects_idle_agent() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    selected = lb.select([a1, a2], action="test")
    assert selected is not None
    assert selected.name in {"a1", "a2"}


@pytest.mark.asyncio
async def test_load_balancer_excludes_non_idle() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    # Poner a1 BUSY.
    await a1.pause()  # PAUSED no es IDLE
    selected = lb.select([a1, a2], action="test")
    assert selected is not None
    assert selected.name == "a2"


@pytest.mark.asyncio
async def test_load_balancer_respects_exclude() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    selected = lb.select([a1, a2], action="test", exclude={"a1"})
    assert selected is not None
    assert selected.name == "a2"


@pytest.mark.asyncio
async def test_load_balancer_returns_none_if_no_eligible() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))
    await a1.pause()
    selected = lb.select([a1], action="test")
    assert selected is None


@pytest.mark.asyncio
async def test_load_balancer_specialty_match() -> None:
    """Agentes con objetivos que matchean la acción tienen ventaja."""
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))  # objectives: stub, test, code
    a2 = await _started(StubAgent("a2"))
    a2._objectives = ["other"]  # noqa: SLF001
    selected = lb.select([a1, a2], action="test")
    assert selected is not None
    assert selected.name == "a1"


@pytest.mark.asyncio
async def test_load_balancer_assign_and_release() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    lb.assign("a1")
    lb.assign("a1")
    assert lb.pending_for("a1") == 2
    lb.release("a1")
    assert lb.pending_for("a1") == 1


@pytest.mark.asyncio
async def test_load_balancer_record_execution_updates_history() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    lb.record_execution("a1", "test", success=True, duration_ms=100.0)
    lb.record_execution("a1", "test", success=False, duration_ms=200.0)
    a1 = await _started(StubAgent("a1"))
    metrics = lb.agent_metrics(a1, action="test")
    assert metrics.total_runs == 2
    assert metrics.success_rate == 0.5
    assert metrics.avg_duration_ms == 150.0


def test_load_balancer_weights_normalized() -> None:
    w = LoadBalancerWeights(w_state=2.0, w_cpu=2.0)
    n = w.normalized()
    total = n.w_state + n.w_cpu + n.w_ram + n.w_queue + n.w_specialty + n.w_history
    assert abs(total - 1.0) < 0.001


@pytest.mark.asyncio
async def test_load_balancer_stats() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    a1 = await _started(StubAgent("a1"))
    lb.select([a1], action="test")
    lb.assign("a1")
    stats = lb.stats()
    assert "weights" in stats
    assert stats["pending_assignments"]["a1"] == 1


@pytest.mark.asyncio
async def test_load_balancer_reset() -> None:
    lb = LoadBalancer(enable_cpu_probe=False)
    lb.record_execution("a1", "test", success=True, duration_ms=10.0)
    lb.assign("a1")
    lb.reset()
    assert lb.stats()["history_entries"] == 0
    assert lb.stats()["pending_assignments"] == {}


# ------------------------------------------------------------------
# Scheduler
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_schedule_returns_empty_for_empty_workflow() -> None:
    from multiagent.workflow.models import Workflow
    scheduler = Scheduler()
    wf = Workflow(name="x")
    decisions = await scheduler.schedule(wf, [])
    assert decisions == []


@pytest.mark.asyncio
async def test_scheduler_schedule_assigns_ready_steps() -> None:
    from multiagent.workflow.models import Step, Workflow
    lb = LoadBalancer(enable_cpu_probe=False)
    scheduler = Scheduler(load_balancer=lb)
    a1 = await _started(StubAgent("a1"))
    wf = Workflow(name="x")
    s1 = Step(name="s1", agent="a1", action="test")
    wf.add_step(s1)
    decisions = await scheduler.schedule(wf, [a1])
    assert len(decisions) == 1
    assert decisions[0].step_id == s1.id
    assert decisions[0].agent_name == "a1"


@pytest.mark.asyncio
async def test_scheduler_respects_max_parallel_steps() -> None:
    from multiagent.workflow.models import Step, Workflow
    lb = LoadBalancer(enable_cpu_probe=False)
    scheduler = Scheduler(
        load_balancer=lb,
        config=SchedulerConfig(max_parallel_steps=1),
    )
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    wf = Workflow(name="x")
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test")
    wf.add_step(s1)
    wf.add_step(s2)
    decisions = await scheduler.schedule(wf, [a1, a2])
    # max_parallel_steps=1 y active_count=0 → sólo 1 decision.
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_scheduler_respects_max_parallel_agents() -> None:
    from multiagent.workflow.models import Step, Workflow
    lb = LoadBalancer(enable_cpu_probe=False)
    scheduler = Scheduler(
        load_balancer=lb,
        config=SchedulerConfig(max_parallel_steps=10, max_parallel_agents=1),
    )
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    wf = Workflow(name="x")
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test")
    wf.add_step(s1)
    wf.add_step(s2)
    decisions = await scheduler.schedule(wf, [a1, a2])
    # max_parallel_agents=1 → sólo 1 decision aunque hay 2 steps listos.
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_scheduler_mark_step_started_and_finished() -> None:
    from multiagent.workflow.enums import StepStatus
    from multiagent.workflow.models import Step, Workflow
    lb = LoadBalancer(enable_cpu_probe=False)
    scheduler = Scheduler(load_balancer=lb)
    await _started(StubAgent("a1"))
    wf = Workflow(name="x")
    s1 = Step(name="s1", agent="a1", action="test")
    wf.add_step(s1)
    await scheduler.mark_step_started(s1, "a1", wf)
    assert s1.status == StepStatus.RUNNING
    assert scheduler.active_count == 1
    await scheduler.mark_step_finished(
        s1, "a1", wf, success=True, duration_ms=42.0
    )
    assert s1.status == StepStatus.COMPLETED
    assert scheduler.active_count == 0


@pytest.mark.asyncio
async def test_scheduler_emits_events() -> None:
    from multiagent.workflow.event_bus import EventBus
    from multiagent.workflow.models import Step, Workflow
    bus = EventBus()
    received: list[str] = []

    async def handler(e):
        received.append(e.type_str)

    await bus.subscribe(handler)
    lb = LoadBalancer(enable_cpu_probe=False)
    scheduler = Scheduler(load_balancer=lb, event_bus=bus)
    a1 = await _started(StubAgent("a1"))
    wf = Workflow(name="x")
    s1 = Step(name="s1", agent="a1", action="test")
    wf.add_step(s1)
    # Trigger workflow start.
    await scheduler.schedule(wf, [a1])
    await scheduler.mark_step_started(s1, "a1", wf)
    await scheduler.mark_step_finished(s1, "a1", wf, success=True, duration_ms=10.0)
    assert "workflow.started" in received
    assert "step.started" in received
    assert "step.finished" in received
    assert "agent.busy" in received
    assert "agent.idle" in received
