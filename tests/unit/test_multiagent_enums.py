"""Tests para enums y transiciones (Sprint 2.1)."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.enums import (  # noqa: E402
    AgentStatus,
    HealthState,
    MessageType,
    Permission,
    Priority,
    TaskStatus,
    can_transition,
    can_transition_task,
    priority_weight,
)


# --- AgentStatus ---


def test_agent_status_values() -> None:
    assert AgentStatus.CREATED.value == "created"
    assert AgentStatus.IDLE.value == "idle"
    assert AgentStatus.BUSY.value == "busy"
    assert AgentStatus.PAUSED.value == "paused"
    assert AgentStatus.ERROR.value == "error"
    assert AgentStatus.STOPPED.value == "stopped"


def test_agent_status_transitions() -> None:
    assert can_transition(AgentStatus.CREATED, AgentStatus.IDLE)
    assert can_transition(AgentStatus.IDLE, AgentStatus.BUSY)
    assert can_transition(AgentStatus.BUSY, AgentStatus.IDLE)
    assert can_transition(AgentStatus.IDLE, AgentStatus.PAUSED)
    assert can_transition(AgentStatus.PAUSED, AgentStatus.IDLE)
    assert can_transition(AgentStatus.IDLE, AgentStatus.STOPPED)


def test_agent_status_invalid_transitions() -> None:
    assert not can_transition(AgentStatus.STOPPED, AgentStatus.IDLE)
    assert not can_transition(AgentStatus.STOPPED, AgentStatus.BUSY)
    assert not can_transition(AgentStatus.CREATED, AgentStatus.BUSY)


# --- TaskStatus ---


def test_task_status_values() -> None:
    assert TaskStatus.QUEUED.value == "queued"
    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.PAUSED.value == "paused"
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"
    assert TaskStatus.CANCELLED.value == "cancelled"


def test_task_status_transitions() -> None:
    assert can_transition_task(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.COMPLETED)
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.PAUSED)
    assert can_transition_task(TaskStatus.PAUSED, TaskStatus.RUNNING)
    assert can_transition_task(TaskStatus.RUNNING, TaskStatus.FAILED)
    assert can_transition_task(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_task_status_invalid_transitions() -> None:
    assert not can_transition_task(TaskStatus.COMPLETED, TaskStatus.RUNNING)
    assert not can_transition_task(TaskStatus.CANCELLED, TaskStatus.RUNNING)


# --- Priority ---


def test_priority_weights() -> None:
    assert priority_weight(Priority.CRITICAL) == 0
    assert priority_weight(Priority.HIGH) == 1
    assert priority_weight(Priority.NORMAL) == 2
    assert priority_weight(Priority.LOW) == 3
    assert priority_weight(Priority.CRITICAL) < priority_weight(Priority.LOW)


# --- MessageType ---


def test_message_types() -> None:
    assert {t.value for t in MessageType} == {
        "request", "response", "event", "error", "system"
    }


# --- Permission ---


def test_permissions() -> None:
    assert Permission.READ_FILES.value == "read_files"
    assert Permission.UNRESTRICTED.value == "unrestricted"
    assert Permission.GIT_OPERATIONS.value == "git_operations"


def test_health_states() -> None:
    assert {s.value for s in HealthState} == {
        "alive", "busy", "idle", "error", "dead"
    }
