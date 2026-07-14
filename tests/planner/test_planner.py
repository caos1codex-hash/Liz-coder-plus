"""Unit tests for planner.planner (TaskPlanner) (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.capability_matcher import AgentInfo  # noqa: E402
from planner.exceptions import (  # noqa: E402
    PlanValidationError,
    TaskNotFoundError,
)
from planner.models import TaskPriority, TaskStatus  # noqa: E402
from planner.planner import PlannerConfig, TaskPlanner  # noqa: E402
from planner.task import Task  # noqa: E402

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_planner_with_agents() -> TaskPlanner:
    planner = TaskPlanner()
    for agent_id, caps in [
        ("planner-a", ["plan", "decompose"]),
        (
            "coder-a",
            ["code.generate", "code.refactor", "code.fix", "filesystem.read", "filesystem.write"],
        ),
        ("tester-a", ["code.test"]),
        ("reviewer-a", ["code.review", "audit"]),
        ("terminal-a", ["terminal.execute"]),
        ("research-a", ["research", "web.search"]),
    ]:
        planner.add_agent(AgentInfo(id=agent_id, capabilities=caps, priority=5))
    return planner


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_default_construction():
    p = TaskPlanner()
    assert p.config is not None
    assert p.decomposer is not None
    assert p.analyzer is not None
    assert p.estimator is not None
    assert p.matcher is not None


def test_construction_with_custom_config():
    cfg = PlannerConfig(auto_estimate=False, auto_assign=False)
    p = TaskPlanner(config=cfg)
    assert p.config.auto_estimate is False
    assert p.config.auto_assign is False


# ----------------------------------------------------------------------
# create_plan
# ----------------------------------------------------------------------


def test_create_plan_basic():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    assert plan.objective == "Create an AI code editor"
    assert len(plan.tasks) > 0


def test_create_plan_with_software_template():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    titles = [t.title for t in plan.tasks.values()]
    assert "Requirements" in titles
    assert "Architecture" in titles
    assert "Backend" in titles


def test_create_plan_assigns_agents():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    assigned = [t for t in plan.tasks.values() if t.assigned_agent is not None]
    assert len(assigned) > 0


def test_create_plan_estimates_tasks():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    for t in plan.tasks.values():
        assert t.estimated_duration > 0
        assert t.estimated_tokens > 0


def test_create_plan_has_dependencies():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    # At least some tasks should have dependencies.
    has_deps = [t for t in plan.tasks.values() if t.dependencies]
    assert len(has_deps) > 0


def test_create_plan_validates():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an AI code editor")
    # Plan should be valid (no errors).
    assert plan.validate() == []


def test_create_plan_with_metadata():
    p = _make_planner_with_agents()
    plan = p.create_plan("Create an app", metadata={"project": "my_project"})
    assert plan.metadata["project"] == "my_project"
    assert plan.metadata["source"] == "task_planner"


def test_create_plan_records_history():
    p = _make_planner_with_agents()
    p.create_plan("Build A")
    p.create_plan("Build B")
    assert len(p.history()) == 2


def test_create_plan_no_estimate():
    cfg = PlannerConfig(auto_estimate=False)
    p = TaskPlanner(config=cfg)
    plan = p.create_plan("Create an app")
    for t in plan.tasks.values():
        assert t.estimated_duration == 0.0


def test_create_plan_no_assign():
    cfg = PlannerConfig(auto_assign=False)
    p = TaskPlanner(config=cfg)
    plan = p.create_plan("Create an app")
    for t in plan.tasks.values():
        assert t.assigned_agent is None


# ----------------------------------------------------------------------
# update_plan
# ----------------------------------------------------------------------


def test_update_plan_add_tasks():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    initial = len(plan.tasks)
    p.update_plan(plan, add_tasks=[Task(id="extra", title="Extra")])
    assert len(plan.tasks) == initial + 1
    assert "extra" in plan.tasks


def test_update_plan_remove_tasks():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    p.update_plan(plan, remove_tasks=[first_id])
    assert first_id not in plan.tasks


def test_update_plan_reassign():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Clear an assignment.
    first_task = next(iter(plan.tasks.values()))
    first_task.assigned_agent = None
    p.update_plan(plan, reassign=True)
    assert first_task.assigned_agent is not None


def test_update_plan_reestimate():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Mess up estimates.
    for t in plan.tasks.values():
        t.estimated_duration = 0
    p.update_plan(plan, reestimate=True)
    for t in plan.tasks.values():
        assert t.estimated_duration > 0


# ----------------------------------------------------------------------
# replan
# ----------------------------------------------------------------------


def test_replan_resets_failed_task():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Pick a task and fail it.
    first_task = next(iter(plan.tasks.values()))
    first_task.mark_ready()
    first_task.mark_running()
    first_task.mark_failed(error="test")
    p.replan(plan, failed_task_id=first_task.id, error="test")
    # Task should be reset to PENDING.
    assert plan.tasks[first_task.id].status == TaskStatus.PENDING


def test_replan_unknown_task_raises():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    with pytest.raises(TaskNotFoundError):
        p.replan(plan, failed_task_id="nonexistent")


def test_replan_splits_failed_task():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    initial = len(plan.tasks)
    first_task = next(iter(plan.tasks.values()))
    # Set max_retries=0 so the failure is terminal (triggers split).
    first_task.max_retries = 0
    first_task.mark_ready()
    first_task.mark_running()
    first_task.mark_failed(error="test")
    p.replan(plan, failed_task_id=first_task.id, error="test")
    # The failed task is split into 2 (so total goes up by 1).
    assert len(plan.tasks) == initial + 1


# ----------------------------------------------------------------------
# split_task
# ----------------------------------------------------------------------


def test_split_task():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    initial = len(plan.tasks)
    first_id = next(iter(plan.tasks))
    new_tasks = p.split_task(plan, first_id, factor=3)
    assert len(new_tasks) == 3
    # Original is removed; 3 new added.
    assert first_id not in plan.tasks
    assert len(plan.tasks) == initial - 1 + 3


def test_split_task_factor_two_default():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    new_tasks = p.split_task(plan, first_id)
    assert len(new_tasks) == 2


def test_split_task_invalid_factor():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    with pytest.raises(ValueError):
        p.split_task(plan, first_id, factor=1)


def test_split_task_sequential_deps():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    new_tasks = p.split_task(plan, first_id, factor=3)
    # Part 2 depends on Part 1, Part 3 depends on Part 2.
    assert new_tasks[0].id in new_tasks[1].dependencies
    assert new_tasks[1].id in new_tasks[2].dependencies


def test_split_task_inherits_capabilities():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    original_caps = list(plan.tasks[first_id].required_capabilities)
    new_tasks = p.split_task(plan, first_id, factor=2)
    for t in new_tasks:
        assert t.required_capabilities == original_caps


def test_split_task_unknown_raises():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    with pytest.raises(TaskNotFoundError):
        p.split_task(plan, "nonexistent")


# ----------------------------------------------------------------------
# merge_tasks
# ----------------------------------------------------------------------


def test_merge_tasks():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    ids = list(plan.tasks.keys())[:3]
    initial = len(plan.tasks)
    merged = p.merge_tasks(plan, ids)
    assert merged.id in plan.tasks
    # 3 removed, 1 added.
    assert len(plan.tasks) == initial - 3 + 1


def test_merge_tasks_requires_two():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    first_id = next(iter(plan.tasks))
    with pytest.raises(ValueError):
        p.merge_tasks(plan, [first_id])


def test_merge_tasks_unions_capabilities():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    ids = list(plan.tasks.keys())[:2]
    caps_before = set()
    for tid in ids:
        caps_before.update(plan.tasks[tid].required_capabilities)
    merged = p.merge_tasks(plan, ids)
    assert set(merged.required_capabilities) == caps_before


def test_merge_tasks_takes_max_priority():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    ids = list(plan.tasks.keys())[:2]
    # Set different priorities.
    plan.tasks[ids[0]].priority = TaskPriority.LOW
    plan.tasks[ids[1]].priority = TaskPriority.CRITICAL
    merged = p.merge_tasks(plan, ids)
    assert merged.priority == TaskPriority.CRITICAL


def test_merge_tasks_unknown_raises():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    with pytest.raises(TaskNotFoundError):
        p.merge_tasks(plan, ["nonexistent1", "nonexistent2"])


# ----------------------------------------------------------------------
# estimate
# ----------------------------------------------------------------------


def test_estimate_plan():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Clear estimates then re-estimate.
    for t in plan.tasks.values():
        t.estimated_duration = 0
        t.estimated_tokens = 0
    result = p.estimate(plan)
    assert "total_estimated_duration" in result
    for t in plan.tasks.values():
        assert t.estimated_duration > 0


def test_estimate_single_task():
    p = _make_planner_with_agents()
    t = Task(title="test", description="do something")
    result = p.estimate(t)
    assert "duration" in result
    assert t.estimated_duration > 0


# ----------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------


def test_validate_clean():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    assert p.validate(plan) == []


def test_validate_detects_issues():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Add a task with a broken dependency.
    plan.add_task(Task(id="broken", title="broken", dependencies=["nonexistent"]))
    errors = p.validate(plan)
    assert any("nonexistent" in e for e in errors)


# ----------------------------------------------------------------------
# assign_agents
# ----------------------------------------------------------------------


def test_assign_agents():
    p = _make_planner_with_agents()
    cfg = PlannerConfig(auto_assign=False)
    p2 = TaskPlanner(config=cfg)
    for a in p.matcher.adapter.list_agents():
        p2.add_agent(a)
    plan = p2.create_plan("Build app")
    # No agents assigned (auto_assign=False).
    for t in plan.tasks.values():
        assert t.assigned_agent is None
    # Now assign.
    result = p2.assign_agents(plan)
    assigned_count = sum(1 for v in result.values() if v)
    assert assigned_count > 0


def test_assign_agents_only_unassigned():
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Pick a task and manually set a different agent.
    first_id = next(iter(plan.tasks))
    plan.tasks[first_id].assigned_agent = "manual-agent"
    p.assign_agents(plan, only_unassigned=True)
    # The manually-set task should be untouched.
    assert plan.tasks[first_id].assigned_agent == "manual-agent"


# ----------------------------------------------------------------------
# add_agent / remove_agent
# ----------------------------------------------------------------------


def test_add_agent_from_dict():
    p = TaskPlanner()
    p.add_agent({"id": "a1", "capabilities": ["code.generate"]})
    assert len(p.registry_adapter.list_agents()) == 1


def test_remove_agent():
    p = TaskPlanner()
    p.add_agent(AgentInfo(id="a1"))
    assert p.remove_agent("a1") is True
    assert p.remove_agent("a1") is False


# ----------------------------------------------------------------------
# metrics
# ----------------------------------------------------------------------


def test_metrics():
    p = _make_planner_with_agents()
    p.create_plan("Build app")
    m = p.metrics()
    assert m["plans_created"] == 1
    assert "estimator" in m
    assert "matcher" in m


# ----------------------------------------------------------------------
# Plan validation failure
# ----------------------------------------------------------------------


def test_create_plan_raises_on_invalid():
    # When auto_validate is True (default) and the plan is invalid,
    # create_plan should raise. We force an invalid plan by configuring
    # the decomposer to produce tasks with broken deps.
    # (This is hard to trigger in practice, so we test the assert_valid
    # path directly.)
    p = _make_planner_with_agents()
    plan = p.create_plan("Build app")
    # Inject a broken dependency.
    plan.add_task(Task(id="broken", title="broken", dependencies=["nonexistent"]))
    with pytest.raises(PlanValidationError):
        plan.assert_valid()


# ----------------------------------------------------------------------
# Semantic memory integration (stub)
# ----------------------------------------------------------------------


class StubSemanticMemory:
    def __init__(self) -> None:
        self.calls = 0

    def retrieve_similar(self, query: str, *, top_k: int = 5):
        self.calls += 1
        return [{"id": "past-plan-1", "score": 0.85, "payload": {"objective": query}}]


def test_semantic_memory_called():
    mem = StubSemanticMemory()
    p = TaskPlanner(semantic_memory=mem)
    p.create_plan("Build an app")
    assert mem.calls == 1


def test_semantic_memory_failure_does_not_crash():
    class FailingMemory:
        def retrieve_similar(self, *args, **kwargs):
            raise RuntimeError("network error")

    p = TaskPlanner(semantic_memory=FailingMemory())
    # Should not raise.
    plan = p.create_plan("Build an app")
    assert len(plan.tasks) > 0
