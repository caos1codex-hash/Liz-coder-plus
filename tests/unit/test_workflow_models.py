"""Tests para Workflow y Step models (Sprint 2.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MULTIAGENT_SRC = ROOT / "packages" / "multiagent" / "src"

sys.path.insert(0, str(MULTIAGENT_SRC))
for _k in [k for k in list(sys.modules) if k == "multiagent" or k.startswith("multiagent.")]:
    sys.modules.pop(_k, None)

from multiagent.enums import Priority  # noqa: E402
from multiagent.workflow.enums import (  # noqa: E402
    StepCriticality,
    StepStatus,
    WorkflowStatus,
)
from multiagent.workflow.models import Step, Workflow  # noqa: E402


# --- Step ---


def test_step_default_values() -> None:
    s = Step(name="x")
    assert s.status == StepStatus.PENDING
    assert s.criticality == StepCriticality.REQUIRED
    assert s.retry == 0
    assert s.max_retries == 3
    assert s.id
    assert s.is_terminal is False


def test_step_transition_pending_to_running() -> None:
    s = Step(name="x")
    s.transition_to(StepStatus.READY)
    s.transition_to(StepStatus.RUNNING)
    assert s.status == StepStatus.RUNNING
    assert s.started_at is not None


def test_step_transition_to_completed_sets_finished_at() -> None:
    s = Step(name="x")
    s.transition_to(StepStatus.READY)
    s.transition_to(StepStatus.RUNNING)
    s.transition_to(StepStatus.COMPLETED)
    assert s.is_completed is True
    assert s.is_terminal is True
    assert s.finished_at is not None


def test_step_invalid_transition_raises() -> None:
    s = Step(name="x")
    # PENDING no puede ir directo a RUNNING.
    with pytest.raises(ValueError, match="cannot transition"):
        s.transition_to(StepStatus.RUNNING)


def test_step_can_retry() -> None:
    s = Step(name="x", max_retries=3)
    s.transition_to(StepStatus.READY)
    s.transition_to(StepStatus.RUNNING)
    s.transition_to(StepStatus.FAILED)
    assert s.can_retry is True
    s.retry = 3
    assert s.can_retry is False


def test_step_record_execution() -> None:
    s = Step(name="x")
    s.record_execution(
        duration_ms=42.0,
        tokens_prompt=10,
        tokens_completion=20,
        tools_used=["file_tool"],
        files_modified=["a.py"],
    )
    assert s.duration_ms == 42.0
    assert s.tokens_prompt == 10
    assert s.tokens_completion == 20
    assert s.tools_used == ["file_tool"]
    assert s.files_modified == ["a.py"]


def test_step_serialization_roundtrip() -> None:
    s = Step(
        name="x",
        agent="coder",
        action="refactor",
        input={"k": 1},
        max_retries=5,
        timeout=30.0,
    )
    s.transition_to(StepStatus.READY)
    s.transition_to(StepStatus.RUNNING)
    s.transition_to(StepStatus.COMPLETED)
    d = s.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "completed"
    s2 = Step.from_dict(d)
    assert s2.name == s.name
    assert s2.status == s.status
    assert s2.id == s.id


# --- Workflow ---


def test_workflow_default_values() -> None:
    wf = Workflow(name="x")
    assert wf.status == WorkflowStatus.PENDING
    assert wf.priority == Priority.NORMAL
    assert wf.step_count == 0
    assert wf.is_terminal is False
    assert wf.progress == 0.0


def test_workflow_add_step() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    wf.add_step(s1)
    assert wf.step_count == 1
    assert s1.id in wf.steps
    assert wf.dag.has_step(s1.id)


def test_workflow_add_step_with_dependencies() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    wf.add_step(s1)
    s2 = Step(name="b", depends_on=[s1.id])
    wf.add_step(s2)
    assert wf.dag.dependencies_of(s2.id) == {s1.id}


def test_workflow_add_step_duplicate_raises() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    wf.add_step(s1)
    with pytest.raises(ValueError, match="already in workflow"):
        wf.add_step(s1)


def test_workflow_remove_step() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b", depends_on=[s1.id])
    wf.add_step(s1)
    wf.add_step(s2)
    assert wf.remove_step(s1.id) is True
    assert wf.step_count == 1
    # s2 ya no depende de s1
    assert s1.id not in s2.depends_on


def test_workflow_add_dependency() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b")
    wf.add_step(s1)
    wf.add_step(s2)
    wf.add_dependency(s2.id, s1.id)
    assert s1.id in s2.depends_on


def test_workflow_add_dependency_missing_step_raises() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    wf.add_step(s1)
    with pytest.raises(KeyError):
        wf.add_dependency(s1.id, "nonexistent")


def test_workflow_validate() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b", depends_on=[s1.id])
    wf.add_step(s1)
    wf.add_step(s2)
    v = wf.validate()
    assert v.ok is True


def test_workflow_validate_cycle() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b")
    wf.add_step(s1)
    wf.add_step(s2)
    wf.add_dependency(s1.id, s2.id)
    wf.add_dependency(s2.id, s1.id)
    v = wf.validate()
    assert v.ok is False
    assert v.cycles


def test_workflow_ready_steps() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b", depends_on=[s1.id])
    s3 = Step(name="c", depends_on=[s2.id])
    for s in [s1, s2, s3]:
        wf.add_step(s)
    # Al inicio, sólo s1 está lista.
    ready = wf.ready_steps()
    assert s1.id in ready
    assert s2.id not in ready
    # Completar s1 → s2 lista.
    s1.transition_to(StepStatus.READY)
    s1.transition_to(StepStatus.RUNNING)
    s1.transition_to(StepStatus.COMPLETED)
    ready = wf.ready_steps()
    assert s2.id in ready


def test_workflow_progress() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b")
    wf.add_step(s1)
    wf.add_step(s2)
    assert wf.progress == 0.0
    s1.transition_to(StepStatus.READY)
    s1.transition_to(StepStatus.RUNNING)
    s1.transition_to(StepStatus.COMPLETED)
    assert wf.progress == 0.5
    s2.transition_to(StepStatus.READY)
    s2.transition_to(StepStatus.RUNNING)
    s2.transition_to(StepStatus.COMPLETED)
    assert wf.progress == 1.0


def test_workflow_transition_to_running_sets_started_at() -> None:
    wf = Workflow(name="x")
    wf.transition_to(WorkflowStatus.RUNNING)
    assert wf.started_at is not None


def test_workflow_transition_to_completed_sets_finished_at() -> None:
    wf = Workflow(name="x")
    wf.transition_to(WorkflowStatus.RUNNING)
    wf.transition_to(WorkflowStatus.COMPLETED)
    assert wf.finished_at is not None
    assert wf.is_terminal is True


def test_workflow_metrics() -> None:
    wf = Workflow(name="x")
    s1 = Step(name="a")
    s2 = Step(name="b")
    wf.add_step(s1)
    wf.add_step(s2)
    s1.record_execution(duration_ms=100.0, tokens_prompt=10, tokens_completion=20, tools_used=["t1"])
    s2.record_execution(duration_ms=200.0, tokens_prompt=5, tokens_completion=15, tools_used=["t1", "t2"])
    s1.transition_to(StepStatus.READY)
    s1.transition_to(StepStatus.RUNNING)
    s1.transition_to(StepStatus.COMPLETED)
    s2.transition_to(StepStatus.READY)
    s2.transition_to(StepStatus.RUNNING)
    s2.transition_to(StepStatus.COMPLETED)
    m = wf.metrics()
    assert m["step_count"] == 2
    assert m["completed"] == 2
    assert m["total_duration_ms"] == 300.0
    assert m["total_tokens_prompt"] == 15
    assert m["total_tokens_completion"] == 35
    assert m["tools_used"]["t1"] == 2
    assert m["tools_used"]["t2"] == 1


def test_workflow_to_dict() -> None:
    wf = Workflow(name="x", description="d")
    wf.add_step(Step(name="a"))
    d = wf.to_dict()
    assert d["name"] == "x"
    assert d["status"] == "pending"
    assert "steps" in d
    assert "dag" in d
    assert "metrics" in d
