"""Unit tests for the Workflow engine.

Sprint 1.7 — Phase 7.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.task import TaskManager, TaskState  # noqa: E402
from src.workflow import (  # noqa: E402
    Workflow,
    WorkflowBuilder,
    WorkflowCancelled,
    WorkflowFailed,
    WorkflowManager,
    WorkflowNode,
    WorkflowState,
)


# ----------------------------------------------------------------------
# Builder
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_sequential_workflow() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("seq")
        .with_task_manager(tm, workflow_id="wf-1")
        .add_task("a", agent_name="echo")
        .add_task("b", depends_on=["a"], agent_name="echo")
        .add_task("c", depends_on=["b"], agent_name="echo")
        .build_async()
    )
    assert wf.node_count == 3
    roots = wf.roots()
    assert len(roots) == 1
    assert roots[0].task.name == "a"


@pytest.mark.asyncio
async def test_builder_parallel_workflow() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("par")
        .with_task_manager(tm, workflow_id="wf-2")
        .add_task("a")
        .add_task("b", depends_on=["a"])
        .add_task("c", depends_on=["a"])
        .add_task("d", depends_on=["b", "c"])
        .build_async()
    )
    assert wf.node_count == 4
    assert len(wf.roots()) == 1
    # Node 'd' has 2 dependencies.
    d_node = next(n for n in wf.nodes if n.task.name == "d")
    assert len(d_node.depends_on) == 2


def test_builder_rejects_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="unknown"):
        WorkflowBuilder("bad").add_task("a", depends_on=["ghost"]).build()


def test_builder_rejects_self_dependency() -> None:
    # Self-deps can only be detected after id resolution; we use the
    # task name for the check too. The builder accepts the name at
    # add_task time, and Workflow validation catches the self-loop.
    from src.task import Task

    t = Task(name="a")
    node = WorkflowNode(task=t, depends_on=[t.id])
    with pytest.raises(ValueError, match="depends on itself"):
        Workflow(name="self", nodes=[node])


def test_builder_rejects_empty_workflow() -> None:
    with pytest.raises(ValueError, match="at least one node"):
        WorkflowBuilder("empty").build()


# ----------------------------------------------------------------------
# DAG validation
# ----------------------------------------------------------------------


def test_workflow_detects_cycle() -> None:
    # Build manually to bypass builder's name resolution.
    from src.task import Task, TaskPriority

    t1 = Task(name="a")
    t2 = Task(name="b")
    t3 = Task(name="c")
    # a -> b -> c -> a
    nodes = [
        WorkflowNode(task=t1, depends_on=[t3.id]),
        WorkflowNode(task=t2, depends_on=[t1.id]),
        WorkflowNode(task=t3, depends_on=[t2.id]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        Workflow(name="cyclic", nodes=nodes)


def test_workflow_to_dict() -> None:
    wf = (
        WorkflowBuilder("simple")
        .add_task("a")
        .add_task("b", depends_on=["a"])
        .build()
    )
    d = wf.to_dict()
    assert d["name"] == "simple"
    assert d["state"] == "created"
    assert d["node_count"] == 2


# ----------------------------------------------------------------------
# Execution: sequential
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sequential_workflow() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("seq")
        .with_task_manager(tm)
        .add_task("a", agent_name="echo")
        .add_task("b", depends_on=["a"], agent_name="echo")
        .add_task("c", depends_on=["b"], agent_name="echo")
        .build_async()
    )
    wfm = WorkflowManager(tm)
    order: list[str] = []

    async def executor(task):
        order.append(task.name)
        return f"r-{task.name}"

    final = await wfm.run(wf, executor)
    assert final.state == WorkflowState.COMPLETED
    assert order == ["a", "b", "c"]


# ----------------------------------------------------------------------
# Execution: parallel
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_parallel_workflow_runs_independent_concurrently() -> None:
    import asyncio
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("par")
        .with_task_manager(tm)
        .add_task("a")
        .add_task("b", depends_on=["a"])
        .add_task("c", depends_on=["a"])
        .add_task("d", depends_on=["b", "c"])
        .build_async()
    )
    wfm = WorkflowManager(tm, max_concurrency=4)
    started: dict[str, float] = {}
    finished: dict[str, float] = {}

    async def executor(task):
        started[task.name] = asyncio.get_event_loop().time()
        await asyncio.sleep(0.05)
        finished[task.name] = asyncio.get_event_loop().time()
        return f"r-{task.name}"

    final = await wfm.run(wf, executor)
    assert final.state == WorkflowState.COMPLETED
    # b and c should overlap.
    overlap = min(finished["b"], finished["c"]) - max(started["b"], started["c"])
    assert overlap > 0, "b and c should run concurrently"


# ----------------------------------------------------------------------
# Execution: failure
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_critical_failure_aborts_workflow() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("fail")
        .with_task_manager(tm)
        .add_task("a", critical=True)
        .add_task("b", depends_on=["a"], critical=True)
        .build_async()
    )
    wfm = WorkflowManager(tm)

    async def executor(task):
        if task.name == "b":
            raise RuntimeError("boom")
        return "ok"

    final = await wfm.run(wf, executor)
    assert final.state == WorkflowState.FAILED
    assert "boom" in (final.error or "")


@pytest.mark.asyncio
async def test_run_non_critical_failure_continues() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("noncrit")
        .with_task_manager(tm)
        .add_task("a", critical=False)
        .add_task("b", depends_on=["a"], critical=True)
        .build_async()
    )
    wfm = WorkflowManager(tm)

    async def executor(task):
        if task.name == "a":
            raise RuntimeError("a failed")
        return "ok"

    final = await wfm.run(wf, executor)
    # 'a' failed (non-critical) → 'b' depends on a → critical path breaks.
    # So workflow should fail because b is critical and its dep failed.
    assert final.state == WorkflowState.FAILED


# ----------------------------------------------------------------------
# Retries
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_retries_failed_node() -> None:
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("retry")
        .with_task_manager(tm)
        .add_task("a", retries=2)
        .build_async()
    )
    wfm = WorkflowManager(tm)
    attempts: list[str] = []

    async def executor(task):
        attempts.append(task.name)
        if len(attempts) < 3:
            raise RuntimeError("not yet")
        return "ok"

    final = await wfm.run(wf, executor)
    assert final.state == WorkflowState.COMPLETED
    assert len(attempts) == 3


# ----------------------------------------------------------------------
# Cancellation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_can_be_cancelled() -> None:
    import asyncio
    tm = TaskManager()
    wf = await (
        WorkflowBuilder("cancel")
        .with_task_manager(tm)
        .add_task("a")
        .add_task("b", depends_on=["a"])
        .build_async()
    )
    wfm = WorkflowManager(tm)
    started = asyncio.Event()

    async def executor(task):
        if task.name == "a":
            started.set()
            await asyncio.sleep(0.2)
        return "ok"

    run_task = asyncio.create_task(wfm.run(wf, executor))
    await started.wait()
    # ``cancel`` is async; await it before awaiting the run task.
    cancelled = await wfm.cancel("cancel", reason="user")
    assert cancelled is True
    final = await run_task
    assert final.state == WorkflowState.CANCELLED


# ----------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_emits_lifecycle_events() -> None:
    bus = EventBus()
    tm = TaskManager(event_bus=bus)
    wf = await (
        WorkflowBuilder("events")
        .with_task_manager(tm)
        .add_task("a")
        .build_async()
    )
    wfm = WorkflowManager(tm, event_bus=bus)
    events: list[tuple[str, Any]] = []
    for et in ("workflow.created", "workflow.started", "workflow.completed"):
        bus.subscribe(et, lambda p, _et=et: events.append((_et, p)))  # type: ignore[misc]

    async def executor(task):
        return "ok"

    await wfm.run(wf, executor)
    types = [e[0] for e in events]
    assert "workflow.created" in types
    assert "workflow.started" in types
    assert "workflow.completed" in types


# ----------------------------------------------------------------------
# Manager: registration
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_register_workflow() -> None:
    tm = TaskManager()
    wf = (
        WorkflowBuilder("reg")
        .add_task("a")
        .build()
    )
    wfm = WorkflowManager(tm)
    await wfm.register(wf)
    assert wfm.count == 1
    assert wfm.get("reg") is wf


@pytest.mark.asyncio
async def test_manager_rejects_duplicate_workflow_name() -> None:
    tm = TaskManager()
    wf1 = WorkflowBuilder("dup").add_task("a").build()
    wf2 = WorkflowBuilder("dup").add_task("b").build()
    wfm = WorkflowManager(tm)
    await wfm.register(wf1)
    with pytest.raises(ValueError, match="already registered"):
        await wfm.register(wf2)
