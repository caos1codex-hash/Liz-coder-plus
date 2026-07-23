"""Unit tests for Sprint 3.6 — Plan Models (PlanState, PlanTask, ExecutablePlan).
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

from src.plan_models import (  # noqa: E402
    ExecutablePlan,
    PlanState,
    PlanTask,
    PlanTaskStatus,
    can_transition_plan,
    can_transition_plan_task,
)


# ======================================================================
# PlanState transitions
# ======================================================================


class TestPlanState:
    """Tests for the Plan state machine."""

    def test_created_to_validating(self) -> None:
        assert can_transition_plan(PlanState.CREATED, PlanState.VALIDATING)

    def test_created_to_cancelled(self) -> None:
        assert can_transition_plan(PlanState.CREATED, PlanState.CANCELLED)

    def test_created_to_running_is_invalid(self) -> None:
        assert not can_transition_plan(PlanState.CREATED, PlanState.RUNNING)

    def test_validating_to_ready(self) -> None:
        assert can_transition_plan(PlanState.VALIDATING, PlanState.READY)

    def test_validating_to_failed(self) -> None:
        assert can_transition_plan(PlanState.VALIDATING, PlanState.FAILED)

    def test_ready_to_running(self) -> None:
        assert can_transition_plan(PlanState.READY, PlanState.RUNNING)

    def test_running_to_completed(self) -> None:
        assert can_transition_plan(PlanState.RUNNING, PlanState.COMPLETED)

    def test_running_to_failed(self) -> None:
        assert can_transition_plan(PlanState.RUNNING, PlanState.FAILED)

    def test_running_to_paused(self) -> None:
        assert can_transition_plan(PlanState.RUNNING, PlanState.PAUSED)

    def test_paused_to_running(self) -> None:
        assert can_transition_plan(PlanState.PAUSED, PlanState.RUNNING)

    def test_completed_is_terminal(self) -> None:
        assert not can_transition_plan(PlanState.COMPLETED, PlanState.RUNNING)
        assert not can_transition_plan(PlanState.COMPLETED, PlanState.FAILED)

    def test_cancelled_is_terminal(self) -> None:
        assert not can_transition_plan(PlanState.CANCELLED, PlanState.READY)

    def test_failed_can_revalidate(self) -> None:
        assert can_transition_plan(PlanState.FAILED, PlanState.VALIDATING)


# ======================================================================
# PlanTaskStatus transitions
# ======================================================================


class TestPlanTaskStatus:
    """Tests for the PlanTask state machine."""

    def test_pending_to_assigned(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.PENDING, PlanTaskStatus.ASSIGNED)

    def test_pending_to_skipped(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.PENDING, PlanTaskStatus.SKIPPED)

    def test_assigned_to_running(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.ASSIGNED, PlanTaskStatus.RUNNING)

    def test_running_to_completed(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.RUNNING, PlanTaskStatus.COMPLETED)

    def test_running_to_failed(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.RUNNING, PlanTaskStatus.FAILED)

    def test_failed_to_assigned(self) -> None:
        assert can_transition_plan_task(PlanTaskStatus.FAILED, PlanTaskStatus.ASSIGNED)

    def test_completed_is_terminal(self) -> None:
        assert not can_transition_plan_task(PlanTaskStatus.COMPLETED, PlanTaskStatus.RUNNING)

    def test_skipped_is_terminal(self) -> None:
        assert not can_transition_plan_task(PlanTaskStatus.SKIPPED, PlanTaskStatus.ASSIGNED)

    def test_pending_to_running_is_invalid(self) -> None:
        assert not can_transition_plan_task(PlanTaskStatus.PENDING, PlanTaskStatus.RUNNING)


# ======================================================================
# PlanTask model
# ======================================================================


class TestPlanTask:
    """Tests for the PlanTask data model."""

    def test_create_task_defaults(self) -> None:
        t = PlanTask(name="read_file")
        assert t.name == "read_file"
        assert t.status == PlanTaskStatus.PENDING
        assert t.retry_count == 0
        assert t.max_retries == 3
        assert t.agent_assigned == ""
        assert t.dependencies == []
        assert t.error is None
        assert t.result is None

    def test_task_transition_updates_state(self) -> None:
        t = PlanTask(name="t")
        t.transition_to(PlanTaskStatus.ASSIGNED)
        assert t.status == PlanTaskStatus.ASSIGNED

    def test_task_invalid_transition_raises(self) -> None:
        t = PlanTask(name="t")
        with pytest.raises(ValueError, match="cannot transition"):
            t.transition_to(PlanTaskStatus.RUNNING)

    def test_task_sets_started_at_on_running(self) -> None:
        t = PlanTask(name="t")
        t.transition_to(PlanTaskStatus.ASSIGNED)
        t.transition_to(PlanTaskStatus.RUNNING)
        assert t.started_at is not None

    def test_task_sets_completed_at_on_completed(self) -> None:
        t = PlanTask(name="t")
        t.transition_to(PlanTaskStatus.ASSIGNED)
        t.transition_to(PlanTaskStatus.RUNNING)
        t.transition_to(PlanTaskStatus.COMPLETED)
        assert t.completed_at is not None

    def test_task_is_terminal(self) -> None:
        t = PlanTask(name="t")
        assert not t.is_terminal
        t.transition_to(PlanTaskStatus.ASSIGNED)
        t.transition_to(PlanTaskStatus.RUNNING)
        t.transition_to(PlanTaskStatus.COMPLETED)
        assert t.is_terminal

    def test_task_can_retry(self) -> None:
        t = PlanTask(name="t", max_retries=2)
        t.transition_to(PlanTaskStatus.ASSIGNED)
        t.transition_to(PlanTaskStatus.RUNNING)
        t.transition_to(PlanTaskStatus.FAILED)
        assert t.can_retry
        t.retry_count = 2
        assert not t.can_retry

    def test_task_to_dict(self) -> None:
        t = PlanTask(
            name="process",
            description="Process the file",
            agent_assigned="coder",
            dependencies=["read"],
        )
        d = t.to_dict()
        assert d["name"] == "process"
        assert d["description"] == "Process the file"
        assert d["agent_assigned"] == "coder"
        assert d["dependencies"] == ["read"]
        assert d["status"] == "pending"
        assert "id" in d
        assert "created_at" in d

    def test_task_to_dict_with_error(self) -> None:
        t = PlanTask(name="t")
        t.transition_to(PlanTaskStatus.ASSIGNED)
        t.transition_to(PlanTaskStatus.RUNNING)
        t.error = "something went wrong"
        t.transition_to(PlanTaskStatus.FAILED)
        d = t.to_dict()
        assert d["error"] == "something went wrong"
        assert d["status"] == "failed"


# ======================================================================
# ExecutablePlan model
# ======================================================================


class TestExecutablePlan:
    """Tests for the ExecutablePlan data model."""

    def _make_plan(self) -> ExecutablePlan:
        plan = ExecutablePlan(
            strategy="file_operation",
            rationale="test plan",
            original_message="Lee el archivo X",
            tasks=[
                PlanTask(name="read", description="Read file"),
                PlanTask(
                    name="process",
                    description="Process data",
                    dependencies=["read"],
                ),
                PlanTask(
                    name="respond",
                    description="Format response",
                    dependencies=["process"],
                ),
            ],
        )
        plan.transition_to(PlanState.VALIDATING)
        plan.transition_to(PlanState.READY)
        plan.transition_to(PlanState.RUNNING)
        return plan

    def test_plan_create(self) -> None:
        plan = ExecutablePlan(strategy="single")
        assert plan.state == PlanState.CREATED
        assert plan.strategy == "single"
        assert plan.tasks == []

    def test_plan_transition_to(self) -> None:
        plan = ExecutablePlan(strategy="test")
        plan.transition_to(PlanState.VALIDATING)
        assert plan.state == PlanState.VALIDATING

    def test_plan_invalid_transition_raises(self) -> None:
        plan = ExecutablePlan(strategy="test")
        with pytest.raises(ValueError, match="cannot transition"):
            plan.transition_to(PlanState.RUNNING)

    def test_plan_task_count(self) -> None:
        plan = self._make_plan()
        assert plan.task_count == 3

    def test_plan_completed_count(self) -> None:
        plan = self._make_plan()
        assert plan.completed_count == 0
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)
        assert plan.completed_count == 1

    def test_plan_failed_count(self) -> None:
        plan = self._make_plan()
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.FAILED)
        assert plan.failed_count == 1

    def test_plan_progress(self) -> None:
        plan = self._make_plan()
        assert plan.progress == 0.0
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)
        assert abs(plan.progress - (1 / 3)) < 0.001

    def test_plan_ready_tasks(self) -> None:
        plan = self._make_plan()
        ready = plan.ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "read"

    def test_plan_ready_tasks_after_first_completed(self) -> None:
        plan = self._make_plan()
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)
        ready = plan.ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "process"

    def test_plan_no_ready_tasks_when_deps_unmet(self) -> None:
        plan = self._make_plan()
        # "respond" depends on "process" which is not done.
        ready = plan.ready_tasks()
        names = [t.name for t in ready]
        assert "respond" not in names

    def test_plan_get_task_by_name(self) -> None:
        plan = self._make_plan()
        t = plan.get_task_by_name("process")
        assert t is not None
        assert t.description == "Process data"

    def test_plan_get_task_by_id(self) -> None:
        plan = self._make_plan()
        task_id = plan.tasks[0].id
        t = plan.get_task(task_id)
        assert t is not None
        assert t.name == "read"

    def test_plan_is_terminal(self) -> None:
        plan = self._make_plan()
        assert not plan.is_terminal
        plan.transition_to(PlanState.COMPLETED)
        assert plan.is_terminal

    def test_plan_metrics(self) -> None:
        plan = self._make_plan()
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)
        plan.tasks[0].execution_time_ms = 100.0
        m = plan.metrics()
        assert m["task_count"] == 3
        assert m["completed"] == 1
        assert m["progress"] > 0
        assert m["total_duration_ms"] == 100.0

    def test_plan_to_dict(self) -> None:
        plan = self._make_plan()
        d = plan.to_dict()
        assert d["strategy"] == "file_operation"
        assert d["state"] == "running"
        assert len(d["tasks"]) == 3
        assert "metrics" in d
        assert "id" in d
        assert "created_at" in d

    def test_plan_started_at_set_on_running(self) -> None:
        plan = self._make_plan()
        assert plan.started_at is not None

    def test_plan_completed_at_set_on_completed(self) -> None:
        plan = self._make_plan()
        plan.transition_to(PlanState.COMPLETED)
        assert plan.completed_at is not None

    def test_plan_empty_has_zero_progress(self) -> None:
        plan = ExecutablePlan(strategy="empty")
        assert plan.progress == 0.0
        assert plan.task_count == 0