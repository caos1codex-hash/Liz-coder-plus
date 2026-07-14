"""Unit tests for planner.scheduler (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.exceptions import (  # noqa: E402
    InvalidTaskError,
    SchedulerError,
    SchedulerPausedError,
)
from planner.execution_plan import ExecutionPlan  # noqa: E402
from planner.models import TaskPriority, TaskStatus  # noqa: E402
from planner.scheduler import Scheduler, SchedulerConfig  # noqa: E402
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_plan() -> ExecutionPlan:
    plan = ExecutionPlan(objective="test")
    plan.add_task(Task(id="t1", title="t1", priority=TaskPriority.HIGH))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    plan.add_task(Task(id="t3", title="t3", dependencies=["t1"]))
    plan.add_task(Task(id="t4", title="t4", dependencies=["t2", "t3"]))
    return plan


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_construction():
    plan = _make_plan()
    sched = Scheduler(plan)
    assert sched.plan is plan
    assert not sched.is_paused
    assert sched.running_count == 0
    assert sched.completed_count == 0


def test_construction_promotes_ready():
    plan = ExecutionPlan()
    # A task with no deps should be READY after construction.
    plan.add_task(Task(id="t1", title="t1"))
    Scheduler(plan)
    assert plan.tasks["t1"].status == TaskStatus.READY


def test_construction_does_not_promote_blocked():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1", dependencies=["t0"]))
    Scheduler(plan)
    assert plan.tasks["t1"].status == TaskStatus.PENDING


# ----------------------------------------------------------------------
# next_tasks
# ----------------------------------------------------------------------


def test_next_tasks_returns_root():
    plan = _make_plan()
    sched = Scheduler(plan)
    batch = sched.next_tasks(max_tasks=4)
    assert len(batch) == 1
    assert batch[0].id == "t1"


def test_next_tasks_respects_max():
    plan = ExecutionPlan()
    plan.add_task(Task(id="a", title="a"))
    plan.add_task(Task(id="b", title="b"))
    plan.add_task(Task(id="c", title="c"))
    sched = Scheduler(plan)
    batch = sched.next_tasks(max_tasks=2)
    assert len(batch) == 2


def test_next_tasks_respects_max_parallel():
    plan = ExecutionPlan()
    plan.add_task(Task(id="a", title="a"))
    plan.add_task(Task(id="b", title="b"))
    plan.add_task(Task(id="c", title="c"))
    sched = Scheduler(plan, config=SchedulerConfig(max_parallel=2))
    # Mark one as running to test the budget.
    sched.mark_started("a")
    batch = sched.next_tasks(max_tasks=10)
    # Budget = 2 - 1 = 1.
    assert len(batch) == 1


def test_next_tasks_priority_order():
    plan = ExecutionPlan()
    plan.add_task(Task(id="low", title="low", priority=TaskPriority.LOW))
    plan.add_task(Task(id="crit", title="crit", priority=TaskPriority.CRITICAL))
    plan.add_task(Task(id="norm", title="norm", priority=TaskPriority.NORMAL))
    sched = Scheduler(plan)
    batch = sched.next_tasks(max_tasks=3)
    # CRITICAL should come first.
    assert batch[0].id == "crit"


def test_next_tasks_zero_returns_empty():
    plan = _make_plan()
    sched = Scheduler(plan)
    assert sched.next_tasks(max_tasks=0) == []


def test_next_tasks_paused_raises():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.pause()
    with pytest.raises(SchedulerPausedError):
        sched.next_tasks()


# ----------------------------------------------------------------------
# mark_started / mark_completed
# ----------------------------------------------------------------------


def test_mark_started():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    assert plan.tasks["t1"].status == TaskStatus.RUNNING
    assert sched.running_count == 1


def test_mark_started_idempotent():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_started("t1")  # should not raise
    assert sched.running_count == 1


def test_mark_completed():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    assert plan.tasks["t1"].status == TaskStatus.COMPLETED
    assert sched.completed_count == 1
    assert sched.running_count == 0


def test_mark_completed_unblocks_dependents():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    # t2 and t3 should now be READY.
    assert plan.tasks["t2"].status == TaskStatus.READY
    assert plan.tasks["t3"].status == TaskStatus.READY


def test_mark_completed_with_result():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1", result={"output": "done"})
    assert plan.tasks["t1"].metadata["result"] == {"output": "done"}


def test_mark_completed_not_running_raises():
    plan = _make_plan()
    sched = Scheduler(plan)
    with pytest.raises(InvalidTaskError):
        sched.mark_completed("t1")


# ----------------------------------------------------------------------
# mark_failed / retry
# ----------------------------------------------------------------------


def test_mark_failed():
    # Use a config with 0 retries so the failure is terminal.
    plan = _make_plan()
    sched = Scheduler(plan, config=SchedulerConfig(default_max_retries=0))
    # Also set the task's max_retries to 0.
    plan.tasks["t1"].max_retries = 0
    sched.mark_started("t1")
    sched.mark_failed("t1", error="boom")
    assert plan.tasks["t1"].status == TaskStatus.FAILED


def test_mark_failed_auto_retries():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_failed("t1", error="transient")
    # Should have been auto-retried (status back to READY).
    assert plan.tasks["t1"].status == TaskStatus.READY
    assert plan.tasks["t1"].retry_count == 1


def test_mark_failed_terminal_after_max_retries():
    plan = _make_plan()
    # Set the task's max_retries to 1 explicitly.
    plan.tasks["t1"].max_retries = 1
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_failed("t1", error="first fail")
    # Auto-retried once (retry_count=1).
    assert plan.tasks["t1"].retry_count == 1
    assert plan.tasks["t1"].status == TaskStatus.READY
    sched.mark_started("t1")
    sched.mark_failed("t1", error="second fail")
    # Now terminal (retry_count == max_retries).
    assert plan.tasks["t1"].status == TaskStatus.FAILED
    assert sched.failed_count == 1


def test_retry_manual():
    plan = _make_plan()
    # Set max_retries=2 so we can retry manually after one auto-retry.
    plan.tasks["t1"].max_retries = 2
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_failed("t1", error="x")
    # Auto-retried once (retry_count=1, status=READY).
    assert plan.tasks["t1"].retry_count == 1
    # Fail again, then manually retry.
    sched.mark_started("t1")
    sched.mark_failed("t1", error="y")
    # Auto-retried again (retry_count=2, status=READY).
    assert plan.tasks["t1"].retry_count == 2
    # No more retries allowed.
    with pytest.raises(InvalidTaskError):
        sched.retry("t1")


def test_retry_when_not_failed_raises():
    plan = _make_plan()
    sched = Scheduler(plan)
    with pytest.raises(InvalidTaskError):
        sched.retry("t1")


def test_critical_failure_pauses_scheduler():
    plan = ExecutionPlan()
    plan.add_task(Task(id="crit", title="crit", priority=TaskPriority.CRITICAL, max_retries=0))
    sched = Scheduler(
        plan,
        config=SchedulerConfig(
            default_max_retries=0,
            pause_on_critical_failure=True,
        ),
    )
    sched.mark_started("crit")
    sched.mark_failed("crit", error="fatal")
    assert sched.is_paused is True


# ----------------------------------------------------------------------
# mark_waiting / resume_task
# ----------------------------------------------------------------------


def test_mark_waiting():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_waiting("t1", reason="waiting on IO")
    assert plan.tasks["t1"].status == TaskStatus.WAITING


def test_resume_task():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_waiting("t1")
    sched.resume_task("t1")
    assert plan.tasks["t1"].status == TaskStatus.RUNNING


def test_resume_task_not_waiting_raises():
    plan = _make_plan()
    sched = Scheduler(plan)
    with pytest.raises(InvalidTaskError):
        sched.resume_task("t1")


# ----------------------------------------------------------------------
# pause / resume / cancel / skip
# ----------------------------------------------------------------------


def test_pause_resume():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.pause()
    assert sched.is_paused
    sched.resume()
    assert not sched.is_paused


def test_pause_idempotent():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.pause()
    sched.pause()
    assert sched.is_paused


def test_resume_idempotent():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.resume()  # not paused
    assert not sched.is_paused


def test_cancel():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.cancel("t1")
    assert plan.tasks["t1"].status == TaskStatus.CANCELLED


def test_cancel_idempotent():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.cancel("t1")
    sched.cancel("t1")  # should not raise


def test_cancel_terminal_noop():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    sched.cancel("t1")  # should not raise
    assert plan.tasks["t1"].status == TaskStatus.COMPLETED


def test_cancel_all():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.cancel_all()
    for t in plan.tasks.values():
        assert t.status == TaskStatus.CANCELLED


def test_skip():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.skip("t1", reason="not needed")
    assert plan.tasks["t1"].status == TaskStatus.SKIPPED


def test_skip_unblocks_dependents():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.skip("t1")
    # t2 and t3 should be READY (skipped counts as "completed" for deps).
    assert plan.tasks["t2"].status == TaskStatus.READY
    assert plan.tasks["t3"].status == TaskStatus.READY


# ----------------------------------------------------------------------
# is_done / progress
# ----------------------------------------------------------------------


def test_is_done_initially_false():
    plan = _make_plan()
    sched = Scheduler(plan)
    assert not sched.is_done()


def test_is_done_true_when_all_terminal():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    plan.add_task(t)
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    assert sched.is_done()


def test_progress_zero_initial():
    plan = _make_plan()
    sched = Scheduler(plan)
    assert sched.progress() == 0.0


def test_progress_one_quarter():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    assert sched.progress() == 0.25


def test_progress_empty_plan():
    plan = ExecutionPlan()
    sched = Scheduler(plan)
    assert sched.progress() == 1.0


# ----------------------------------------------------------------------
# Recovery (to_state / from_state)
# ----------------------------------------------------------------------


def test_to_state_structure():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    state = sched.to_state()
    assert state["plan_id"] == plan.id
    assert "t1" in state["running"]
    assert state["paused"] is False


def test_from_state_restores():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    state = sched.to_state()
    # New scheduler from same plan + state.
    sched2 = Scheduler.from_state(plan, state)
    assert sched2.completed_count == 1
    assert "t1" in sched2._completed


def test_from_state_rejects_wrong_plan():
    plan = _make_plan()
    state = {"plan_id": "different-plan"}
    with pytest.raises(SchedulerError):
        Scheduler.from_state(plan, state)


# ----------------------------------------------------------------------
# History / metrics
# ----------------------------------------------------------------------


def test_history_records_events():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    h = sched.history()
    events = [e["event"] for e in h]
    assert "task.started" in events
    assert "task.completed" in events


def test_metrics():
    plan = _make_plan()
    sched = Scheduler(plan)
    sched.mark_started("t1")
    sched.mark_completed("t1")
    m = sched.metrics()
    assert m["total_tasks"] == 4
    assert m["completed"] == 1
    assert m["progress"] == 0.25
    assert m["is_done"] is False


# ----------------------------------------------------------------------
# on_replan callback
# ----------------------------------------------------------------------


def test_on_replan_called_on_terminal_failure():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1", max_retries=0))
    calls: list[tuple] = []

    def on_replan(p, t, *, error=""):
        calls.append((p.id, t.id, error))

    sched = Scheduler(
        plan,
        config=SchedulerConfig(
            default_max_retries=0,
            enable_replan_on_failure=True,
            pause_on_critical_failure=False,
        ),
        on_replan=on_replan,
    )
    sched.mark_started("t1")
    sched.mark_failed("t1", error="boom")
    assert len(calls) == 1
    assert calls[0][1] == "t1"
    assert calls[0][2] == "boom"


# ----------------------------------------------------------------------
# Full execution simulation
# ----------------------------------------------------------------------


def test_full_execution():
    plan = _make_plan()
    sched = Scheduler(plan)
    # Execute all tasks in dependency order.
    while not sched.is_done():
        batch = sched.next_tasks(max_tasks=4)
        if not batch:
            break
        for t in batch:
            sched.mark_started(t.id)
            sched.mark_completed(t.id)
    assert sched.is_done()
    assert sched.progress() == 1.0
    assert sched.completed_count == 4
