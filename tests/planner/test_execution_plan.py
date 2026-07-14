"""Unit tests for planner.execution_plan (Sprint 2.8)."""

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
    PlanValidationError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from planner.execution_plan import ExecutionPlan  # noqa: E402
from planner.models import TaskPriority, TaskStatus  # noqa: E402
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_plan():
    plan = ExecutionPlan()
    assert plan.id.startswith("plan-")
    assert plan.objective == ""
    assert len(plan.tasks) == 0


def test_plan_with_objective():
    plan = ExecutionPlan(objective="Build X")
    assert plan.objective == "Build X"


def test_plan_rebuilds_graph_from_tasks_on_init():
    t1 = Task(id="t1", title="t1")
    t2 = Task(id="t2", title="t2", dependencies=["t1"])
    plan = ExecutionPlan(tasks={"t1": t1, "t2": t2})
    assert plan.graph.has_task("t1")
    assert plan.graph.has_task("t2")
    assert plan.graph.parents("t2") == ["t1"]


def test_plan_rejects_non_dag_graph():
    with pytest.raises(TypeError):
        ExecutionPlan(graph="not a dag")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# add_task / remove_task
# ----------------------------------------------------------------------


def test_add_task():
    plan = ExecutionPlan()
    t = Task(id="t1", title="Task 1")
    plan.add_task(t)
    assert "t1" in plan
    assert len(plan) == 1


def test_add_task_duplicate_raises():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="Task 1"))
    with pytest.raises(TaskAlreadyExistsError):
        plan.add_task(Task(id="t1", title="Duplicate"))


def test_add_task_with_known_dep_adds_edge():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    assert plan.graph.parents("t2") == ["t1"]


def test_remove_task():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    assert plan.remove_task("t1") is True
    assert "t1" not in plan
    # t2's dependencies list is cleaned up.
    assert "t1" not in plan.tasks["t2"].dependencies


def test_remove_task_missing_returns_false():
    plan = ExecutionPlan()
    assert plan.remove_task("nope") is False


def test_clear():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.clear()
    assert len(plan) == 0
    assert len(plan.graph) == 0


# ----------------------------------------------------------------------
# add_dependency / remove_dependency
# ----------------------------------------------------------------------


def test_add_dependency():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2"))
    plan.add_dependency("t2", "t1")
    assert plan.graph.parents("t2") == ["t1"]
    assert "t1" in plan.tasks["t2"].dependencies


def test_add_dependency_missing_task_raises():
    plan = ExecutionPlan()
    with pytest.raises(TaskNotFoundError):
        plan.add_dependency("t2", "t1")


def test_remove_dependency():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    plan.remove_dependency("t2", "t1")
    assert plan.graph.parents("t2") == []


# ----------------------------------------------------------------------
# Queries
# ----------------------------------------------------------------------


def test_get_task():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    plan.add_task(t)
    assert plan.get_task("t1") is t
    assert plan.get_task("missing") is None


def test_require_task():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    assert plan.require_task("t1").title == "t1"
    with pytest.raises(TaskNotFoundError):
        plan.require_task("missing")


def test_iteration():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2"))
    titles = [t.title for t in plan]
    assert set(titles) == {"t1", "t2"}


def test_task_ids_sorted():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t2", title="t2"))
    plan.add_task(Task(id="t1", title="t1"))
    assert plan.task_ids == ["t1", "t2"]


# ----------------------------------------------------------------------
# Cached views
# ----------------------------------------------------------------------


def test_execution_order():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    plan.add_task(Task(id="t3", title="t3", dependencies=["t2"]))
    order = plan.execution_order
    assert order == ["t1", "t2", "t3"]


def test_parallel_groups():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2"))
    plan.add_task(Task(id="t3", title="t3", dependencies=["t1", "t2"]))
    groups = plan.parallel_groups
    assert set(groups[0]) == {"t1", "t2"}
    assert groups[1] == ["t3"]


def test_statistics_initial():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2"))
    stats = plan.statistics
    assert stats.total_tasks == 2
    assert stats.pending == 2
    assert stats.completed == 0


def test_statistics_reflects_status():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    plan.add_task(t)
    t.mark_ready()
    t.mark_running()
    t.mark_completed()
    stats = plan.statistics
    assert stats.completed == 1
    assert stats.pending == 0


def test_statistics_reflects_estimates():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    t.estimated_duration = 5.0
    t.estimated_tokens = 100
    t.estimated_cost = 0.01
    plan.add_task(t)
    stats = plan.statistics
    assert stats.total_estimated_duration == 5.0
    assert stats.total_estimated_tokens == 100
    assert stats.total_estimated_cost == 0.01


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_validate_clean():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    assert plan.validate() == []


def test_validate_broken_dependency():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1", dependencies=["missing"]))
    errors = plan.validate()
    assert any("missing" in e for e in errors)


def test_validate_invalid_capability():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    t.required_capabilities = ["ok", ""]
    plan.add_task(t)
    errors = plan.validate()
    assert any("invalid required_capability" in e for e in errors)


def test_assert_valid_raises():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1", dependencies=["missing"]))
    with pytest.raises(PlanValidationError):
        plan.assert_valid()


def test_assert_valid_passes():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.assert_valid()  # should not raise


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------


def test_summary():
    plan = ExecutionPlan(objective="Test")
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    s = plan.summary()
    assert s["objective"] == "Test"
    assert s["task_count"] == 2
    assert "statistics" in s
    assert "parallel_groups" in s
    assert "execution_order" in s
    assert "critical_path" in s


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def test_serialize_roundtrip():
    plan = ExecutionPlan(objective="Round trip")
    plan.add_task(Task(id="t1", title="t1", priority=TaskPriority.HIGH))
    plan.add_task(Task(id="t2", title="t2", dependencies=["t1"]))
    s = plan.serialize()
    plan2 = ExecutionPlan.deserialize(s)
    assert plan2.objective == "Round trip"
    assert len(plan2.tasks) == 2
    assert plan2.tasks["t1"].priority == TaskPriority.HIGH
    assert plan2.graph.parents("t2") == ["t1"]


def test_serialize_empty_plan():
    plan = ExecutionPlan(objective="Empty")
    s = plan.serialize()
    plan2 = ExecutionPlan.deserialize(s)
    assert plan2.objective == "Empty"
    assert len(plan2.tasks) == 0


# ----------------------------------------------------------------------
# Metadata / clone
# ----------------------------------------------------------------------


def test_update_metadata():
    plan = ExecutionPlan()
    plan.update_metadata(source="test", version="1.0")
    assert plan.metadata["source"] == "test"
    assert plan.metadata["version"] == "1.0"


def test_clone_new_id():
    plan = ExecutionPlan(objective="Original")
    plan.add_task(Task(id="t1", title="t1"))
    clone = plan.clone()
    assert clone.id != plan.id
    assert clone.objective == "Original"
    assert len(clone.tasks) == 1


def test_clone_preserve_id():
    plan = ExecutionPlan(objective="Original")
    clone = plan.clone(new_id=False)
    assert clone.id == plan.id


def test_update_task_status():
    plan = ExecutionPlan()
    t = Task(id="t1", title="t1")
    plan.add_task(t)
    plan.update_task_status("t1", TaskStatus.COMPLETED)
    assert t.status == TaskStatus.COMPLETED


def test_update_task_status_missing_raises():
    plan = ExecutionPlan()
    with pytest.raises(TaskNotFoundError):
        plan.update_task_status("missing", TaskStatus.COMPLETED)


# ----------------------------------------------------------------------
# __repr__ / __contains__ / __len__
# ----------------------------------------------------------------------


def test_repr():
    plan = ExecutionPlan(objective="My Plan")
    plan.add_task(Task(id="t1", title="t1"))
    r = repr(plan)
    assert "ExecutionPlan" in r
    assert "My Plan" in r


def test_contains():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    assert "t1" in plan
    assert "missing" not in plan


def test_len():
    plan = ExecutionPlan()
    plan.add_task(Task(id="t1", title="t1"))
    plan.add_task(Task(id="t2", title="t2"))
    assert len(plan) == 2
