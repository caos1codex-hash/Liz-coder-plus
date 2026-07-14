"""Unit tests for planner.exceptions (Sprint 2.8)."""

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
    AgentAssignmentError,
    CycleDetectedError,
    DependencyError,
    EstimationError,
    InvalidTaskError,
    PlanValidationError,
    PlannerError,
    SchedulerError,
    SchedulerPausedError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)

# ----------------------------------------------------------------------
# Hierarchy
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TaskNotFoundError,
        TaskAlreadyExistsError,
        InvalidTaskError,
        CycleDetectedError,
        DependencyError,
        PlanValidationError,
        SchedulerError,
        SchedulerPausedError,
        AgentAssignmentError,
        EstimationError,
    ],
)
def test_all_inherit_from_planner_error(exc):
    assert issubclass(exc, PlannerError)


def test_scheduler_paused_inherits_scheduler_error():
    assert issubclass(SchedulerPausedError, SchedulerError)


# ----------------------------------------------------------------------
# TaskNotFoundError
# ----------------------------------------------------------------------


def test_task_not_found_carries_id():
    e = TaskNotFoundError("abc")
    assert e.task_id == "abc"
    assert "abc" in str(e)
    assert e.to_dict()["details"]["task_id"] == "abc"


def test_task_not_found_custom_message():
    e = TaskNotFoundError("x", message="custom")
    assert "custom" in str(e)


# ----------------------------------------------------------------------
# TaskAlreadyExistsError
# ----------------------------------------------------------------------


def test_task_already_exists_carries_id():
    e = TaskAlreadyExistsError("dup")
    assert e.task_id == "dup"
    assert "dup" in str(e)


# ----------------------------------------------------------------------
# InvalidTaskError
# ----------------------------------------------------------------------


def test_invalid_task_carries_reason():
    e = InvalidTaskError("t1", "bad transition")
    assert e.task_id == "t1"
    assert e.reason == "bad transition"
    assert "t1" in str(e)
    assert "bad transition" in str(e)


# ----------------------------------------------------------------------
# CycleDetectedError
# ----------------------------------------------------------------------


def test_cycle_detected_repr():
    e = CycleDetectedError(["a", "b", "c"])
    assert e.cycle == ["a", "b", "c"]
    s = str(e)
    assert "a" in s and "b" in s and "c" in s


def test_cycle_detected_empty():
    e = CycleDetectedError([])
    assert e.cycle == []
    # Should not crash on empty.
    assert isinstance(str(e), str)


# ----------------------------------------------------------------------
# DependencyError
# ----------------------------------------------------------------------


def test_dependency_error():
    e = DependencyError("child", "parent", reason="missing")
    assert e.task_id == "child"
    assert e.dependency_id == "parent"
    assert "child" in str(e) or "parent" in str(e)


# ----------------------------------------------------------------------
# PlanValidationError
# ----------------------------------------------------------------------


def test_plan_validation_error_collects_errors():
    e = PlanValidationError(["err1", "err2"], plan_id="plan-1")
    assert e.errors == ["err1", "err2"]
    assert e.plan_id == "plan-1"
    assert "err1" in str(e)
    assert "err2" in str(e)


def test_plan_validation_error_empty():
    e = PlanValidationError([])
    assert e.errors == []
    assert isinstance(str(e), str)


# ----------------------------------------------------------------------
# to_dict
# ----------------------------------------------------------------------


def test_to_dict_structure():
    e = PlannerError("msg", details={"k": "v"})
    d = e.to_dict()
    assert d["error"] == "PlannerError"
    assert d["message"] == "msg"
    assert d["details"] == {"k": "v"}


def test_str_with_details():
    e = PlannerError("msg", details={"k": "v"})
    s = str(e)
    assert "msg" in s
    assert "k" in s


def test_str_no_message():
    e = PlannerError()
    assert str(e) == "PlannerError"


def test_str_no_details():
    e = PlannerError("only message")
    assert str(e) == "only message"


# ----------------------------------------------------------------------
# AgentAssignmentError
# ----------------------------------------------------------------------


def test_agent_assignment_error():
    e = AgentAssignmentError("t1", reason="no agents")
    assert e.task_id == "t1"
    assert "t1" in str(e)


# ----------------------------------------------------------------------
# EstimationError
# ----------------------------------------------------------------------


def test_estimation_error():
    e = EstimationError("bad config")
    assert "bad config" in str(e)
