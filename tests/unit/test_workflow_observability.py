"""Tests para MetricsCollector y planner_ext (Sprint 2.2)."""

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
from multiagent.workflow.engine import FailoverPolicy, WorkflowEngine  # noqa: E402
from multiagent.workflow.event_bus import EventBus  # noqa: E402
from multiagent.workflow.models import Step, Workflow  # noqa: E402
from multiagent.workflow.observability import Histogram, MetricsCollector  # noqa: E402
from multiagent.workflow.planner_ext import plan_workflow  # noqa: E402


# ------------------------------------------------------------------
# Histogram
# ------------------------------------------------------------------


def test_histogram_record_and_stats() -> None:
    h = Histogram()
    for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
        h.record(v)
    assert h.count == 5
    assert h.min == 10.0
    assert h.max == 50.0
    assert h.mean() == 30.0
    assert h.percentile(50) == 30.0
    assert h.percentile(95) == 50.0


def test_histogram_empty() -> None:
    h = Histogram()
    assert h.count == 0
    assert h.mean() == 0.0
    assert h.percentile(50) == 0.0


def test_histogram_to_dict() -> None:
    h = Histogram()
    h.record(1.0)
    h.record(2.0)
    d = h.to_dict()
    assert d["count"] == 2
    assert d["mean"] == 1.5
    assert d["min"] == 1.0
    assert d["max"] == 2.0
    assert "p50" in d
    assert "p95" in d
    assert "p99" in d


# ------------------------------------------------------------------
# MetricsCollector
# ------------------------------------------------------------------


class StubAgent(BaseAgent):
    default_permissions = frozenset({Permission.MEMORY_WRITE})
    default_objectives = ["stub", "test"]

    def __init__(self, name: str = "stub", *, fail: bool = False, **kwargs):
        super().__init__(name=name, description="stub", **kwargs)
        self._fail = fail

    async def _execute_impl(self, task: dict) -> dict:
        if self._fail:
            raise RuntimeError("boom")
        return {"value": task.get("name"), "tools_used": []}


async def _started(agent: BaseAgent) -> BaseAgent:
    await agent.start()
    return agent


@pytest.mark.asyncio
async def test_metrics_collector_basic_workflow() -> None:
    a1 = await _started(StubAgent("a1"))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(agents=[a1], event_bus=bus)
    s1 = Step(name="s1", agent="a1", action="test")
    wf = Workflow(name="my_wf")
    wf.add_step(s1)
    await engine.run(wf)
    # Esperar a que el collector procese los eventos.
    await asyncio.sleep(0.05)
    m = mc.metrics()
    assert m["workflow_counts"]["started"] == 1
    assert m["workflow_counts"]["completed"] == 1
    # workflow_duration se agrupa por nombre (puede ser "unknown" si el
    # evento no incluye el nombre).
    assert len(m["workflow_duration"]) >= 1
    duration_entry = list(m["workflow_duration"].values())[0]
    assert duration_entry["count"] == 1
    await mc.stop()


@pytest.mark.asyncio
async def test_metrics_collector_step_duration() -> None:
    a1 = await _started(StubAgent("a1"))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(agents=[a1], event_bus=bus)
    s1 = Step(name="s1", agent="a1", action="my_action")
    wf = Workflow(name="x")
    wf.add_step(s1)
    await engine.run(wf)
    await asyncio.sleep(0.05)
    m = mc.metrics()
    # step_duration se registra por action.
    assert "my_action" in m["step_duration"] or "unknown" in m["step_duration"]
    await mc.stop()


@pytest.mark.asyncio
async def test_metrics_collector_retry_count() -> None:
    a1 = await _started(StubAgent("a1", fail=True))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(
        agents=[a1], event_bus=bus,
        failover=FailoverPolicy(max_retries=2, reassign_on_fail=False,
                                 cancel_on_required_fail=False),
    )
    s1 = Step(name="s1", agent="a1", action="test", max_retries=2)
    wf = Workflow(name="x")
    wf.add_step(s1)
    await engine.run(wf)
    await asyncio.sleep(0.05)
    m = mc.metrics()
    assert m["retry_count"] >= 1
    await mc.stop()


@pytest.mark.asyncio
async def test_metrics_collector_parallelism() -> None:
    from multiagent.workflow.scheduler import SchedulerConfig
    a1 = await _started(StubAgent("a1"))
    a2 = await _started(StubAgent("a2"))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(
        agents=[a1, a2], event_bus=bus,
        scheduler_config=SchedulerConfig(max_parallel_steps=2, max_parallel_agents=2),
    )
    s1 = Step(name="s1", agent="a1", action="test")
    s2 = Step(name="s2", agent="a2", action="test")
    wf = Workflow(name="x")
    wf.add_step(s1)
    wf.add_step(s2)
    await engine.run(wf)
    await asyncio.sleep(0.05)
    m = mc.metrics()
    assert m["parallelism"]["max"] >= 1
    await mc.stop()


@pytest.mark.asyncio
async def test_metrics_collector_success_rate() -> None:
    a1 = await _started(StubAgent("a1"))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(agents=[a1], event_bus=bus)
    s1 = Step(name="s1", agent="a1", action="test")
    wf = Workflow(name="x")
    wf.add_step(s1)
    await engine.run(wf)
    await asyncio.sleep(0.05)
    m = mc.metrics()
    assert m["success_rate"]["workflow"] == 1.0
    assert "a1" in m["success_rate"]["by_agent"]
    assert m["success_rate"]["by_agent"]["a1"] == 1.0
    await mc.stop()


@pytest.mark.asyncio
async def test_metrics_collector_reset() -> None:
    a1 = await _started(StubAgent("a1"))
    bus = EventBus()
    mc = MetricsCollector(bus)
    await mc.start()
    engine = WorkflowEngine(agents=[a1], event_bus=bus)
    s1 = Step(name="s1", agent="a1", action="test")
    wf = Workflow(name="x")
    wf.add_step(s1)
    await engine.run(wf)
    await asyncio.sleep(0.05)
    await mc.reset()
    m = mc.metrics()
    assert m["workflow_counts"] == {}
    assert m["retry_count"] == 0
    await mc.stop()


# ------------------------------------------------------------------
# planner_ext
# ------------------------------------------------------------------


def test_plan_workflow_file_op() -> None:
    from multiagent.enums import Priority
    wf, plan = plan_workflow(
        "lee el archivo config.json y resume su contenido",
        priority=Priority.NORMAL,
    )
    assert plan["strategy"] == "file_op"
    assert plan["estimated_difficulty"] == 2
    assert len(wf.steps) == 3
    assert "estimates" in plan
    assert plan["estimates"]["step_count"] == 3
    assert plan["estimates"]["total_tokens"] > 0


def test_plan_workflow_terminal() -> None:
    wf, plan = plan_workflow("ejecuta el comando ls -la")
    assert plan["strategy"] == "terminal"
    assert len(wf.steps) == 3


def test_plan_workflow_research() -> None:
    wf, plan = plan_workflow("investiga fastapi y luego resume sus features")
    assert plan["strategy"] == "research"
    assert len(wf.steps) == 3


def test_plan_workflow_review() -> None:
    wf, plan = plan_workflow("revisa el código del archivo main.py")
    assert plan["strategy"] == "review"
    assert len(wf.steps) == 3


def test_plan_workflow_system() -> None:
    wf, plan = plan_workflow("información del sistema")
    assert plan["strategy"] == "system"
    assert len(wf.steps) == 2


def test_plan_workflow_fallback_single() -> None:
    wf, plan = plan_workflow("hello world")
    assert plan["strategy"] == "single"
    assert len(wf.steps) == 1
    assert plan["estimated_difficulty"] == 1


def test_plan_workflow_dag_has_dependencies() -> None:
    wf, plan = plan_workflow("lee el archivo config.json")
    # En un patrón file_op, los steps tienen dependencias lineales.
    steps = list(wf.steps.values())
    # El primer step no tiene dependencias; los siguientes sí.
    assert not steps[0].depends_on
    assert steps[1].depends_on
    assert steps[2].depends_on


def test_plan_workflow_estimates_have_tokens() -> None:
    wf, plan = plan_workflow("lee el archivo config.json")
    est = plan["estimates"]
    assert est["total_tokens_prompt"] > 0
    assert est["total_tokens_completion"] > 0
    assert est["total_tokens"] == est["total_tokens_prompt"] + est["total_tokens_completion"]


def test_plan_workflow_estimates_parallelism() -> None:
    wf, plan = plan_workflow("lee el archivo config.json")
    # Workflow lineal: parallelism = 1.
    assert plan["estimates"]["parallelism_factor"] == 1.0
    # Duración paralela = duración secuencial (no hay paralelismo).
    assert plan["estimates"]["estimated_duration_ms_parallel"] == plan["estimates"]["total_duration_ms_sequential"]


def test_plan_workflow_priority_propagates() -> None:
    from multiagent.enums import Priority
    wf, plan = plan_workflow("test", priority=Priority.HIGH)
    assert wf.priority == Priority.HIGH


def test_plan_workflow_workflow_valid_dag() -> None:
    wf, plan = plan_workflow("lee el archivo config.json")
    v = wf.validate()
    assert v.ok is True
    assert v.cycles == []
