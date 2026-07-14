"""Unit tests for planner.models (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.models import (  # noqa: E402
    AgentAssignment,
    CapabilityScore,
    Difficulty,
    PlanStatistics,
    TaskPriority,
    TaskStatus,
    can_transition,
    difficulty_multiplier,
    priority_weight,
)

# ----------------------------------------------------------------------
# TaskStatus
# ----------------------------------------------------------------------


def test_status_values():
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.COMPLETED.value == "completed"


def test_status_is_terminal():
    assert TaskStatus.is_terminal(TaskStatus.COMPLETED)
    assert TaskStatus.is_terminal(TaskStatus.FAILED)
    assert TaskStatus.is_terminal(TaskStatus.CANCELLED)
    assert TaskStatus.is_terminal(TaskStatus.SKIPPED)
    assert not TaskStatus.is_terminal(TaskStatus.RUNNING)
    assert not TaskStatus.is_terminal(TaskStatus.PENDING)


def test_status_is_active():
    assert TaskStatus.is_active(TaskStatus.RUNNING)
    assert not TaskStatus.is_active(TaskStatus.COMPLETED)


# ----------------------------------------------------------------------
# can_transition
# ----------------------------------------------------------------------


def test_can_transition_pending_to_ready():
    assert can_transition(TaskStatus.PENDING, TaskStatus.READY)


def test_can_transition_ready_to_running():
    assert can_transition(TaskStatus.READY, TaskStatus.RUNNING)


def test_can_transition_running_to_completed():
    assert can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)


def test_can_transition_running_to_failed():
    assert can_transition(TaskStatus.RUNNING, TaskStatus.FAILED)


def test_cannot_transition_completed_to_running():
    assert not can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)


def test_cannot_transition_pending_to_running():
    assert not can_transition(TaskStatus.PENDING, TaskStatus.RUNNING)


def test_failed_can_retry_to_ready():
    assert can_transition(TaskStatus.FAILED, TaskStatus.READY)


# ----------------------------------------------------------------------
# TaskPriority
# ----------------------------------------------------------------------


def test_priority_weight_ordering():
    assert priority_weight(TaskPriority.CRITICAL) > priority_weight(TaskPriority.HIGH)
    assert priority_weight(TaskPriority.HIGH) > priority_weight(TaskPriority.NORMAL)
    assert priority_weight(TaskPriority.NORMAL) > priority_weight(TaskPriority.LOW)
    assert priority_weight(TaskPriority.LOW) > priority_weight(TaskPriority.BACKGROUND)


def test_priority_values():
    assert TaskPriority.CRITICAL.value == "critical"
    assert TaskPriority.BACKGROUND.value == "background"


# ----------------------------------------------------------------------
# Difficulty
# ----------------------------------------------------------------------


def test_difficulty_multiplier_ordering():
    assert difficulty_multiplier(Difficulty.TRIVIAL) < difficulty_multiplier(Difficulty.EASY)
    assert difficulty_multiplier(Difficulty.EASY) < difficulty_multiplier(Difficulty.MEDIUM)
    assert difficulty_multiplier(Difficulty.MEDIUM) < difficulty_multiplier(Difficulty.HARD)
    assert difficulty_multiplier(Difficulty.HARD) < difficulty_multiplier(Difficulty.EXPERT)


def test_difficulty_values():
    assert Difficulty.TRIVIAL.value == "trivial"
    assert Difficulty.EXPERT.value == "expert"


# ----------------------------------------------------------------------
# PlanStatistics
# ----------------------------------------------------------------------


def test_plan_statistics_defaults():
    s = PlanStatistics()
    assert s.total_tasks == 0
    assert s.pending == 0
    assert s.completed == 0
    assert s.total_estimated_duration == 0.0
    assert s.parallel_groups == 0


def test_plan_statistics_to_dict():
    s = PlanStatistics(total_tasks=5, completed=2, failed=1)
    d = s.to_dict()
    assert d["total_tasks"] == 5
    assert d["by_status"]["completed"] == 2
    assert d["by_status"]["failed"] == 1


# ----------------------------------------------------------------------
# CapabilityScore / AgentAssignment
# ----------------------------------------------------------------------


def test_capability_score_to_dict():
    s = CapabilityScore(agent_id="a1", score=0.85, specialty=1.0)
    d = s.to_dict()
    assert d["agent_id"] == "a1"
    assert d["score"] == 0.85
    assert d["specialty"] == 1.0


def test_agent_assignment_to_dict():
    a = AgentAssignment(
        task_id="t1",
        agent_id="a1",
        assigned=True,
        score=0.9,
        candidates=[("a1", 0.9), ("a2", 0.5)],
    )
    d = a.to_dict()
    assert d["task_id"] == "t1"
    assert d["agent_id"] == "a1"
    assert d["assigned"] is True
    assert len(d["candidates"]) == 2
    assert d["candidates"][0]["agent_id"] == "a1"


def test_agent_assignment_default_unassigned():
    a = AgentAssignment(task_id="t1")
    assert a.assigned is False
    assert a.agent_id is None
