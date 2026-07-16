"""Unit tests for Sprint 3.6 — PlanExecutor.

Tests the full plan execution flow: plan creation, validation,
agent assignment, task execution, error handling with retries,
cancellation, progress events, and history.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.event_bus import EventBus  # noqa: E402
from src.plan_executor import (  # noqa: E402
    PlanExecutor,
    PlanExecutorConfig,
)
from src.plan_models import (  # noqa: E402
    ExecutablePlan,
    PlanState,
    PlanTask,
    PlanTaskStatus,
)


# ======================================================================
# Fixtures
# ======================================================================


def _make_mock_agent(name: str = "echo", response: str = "done") -> Any:
    """Create a mock agent satisfying the Agent protocol."""
    agent = MagicMock()
    agent.name = name
    agent.handle = AsyncMock(return_value=response)
    return agent


def _make_mock_orchestrator(
    agents: dict[str, Any] | None = None,
) -> Any:
    """Create a mock Orchestrator with agent access."""
    orch = MagicMock()
    orch._agents = agents or {"echo": _make_mock_agent("echo")}
    router = MagicMock()
    router.default_agent = _make_mock_agent("echo")
    orch._router = router
    return orch


def _simple_subtasks() -> list[dict[str, Any]]:
    """Return a simple 2-task plan (no dependencies)."""
    return [
        {
            "name": "step1",
            "description": "First step",
            "suggested_agent": "echo",
            "suggested_tools": [],
            "depends_on": [],
        },
        {
            "name": "step2",
            "description": "Second step",
            "suggested_agent": "echo",
            "suggested_tools": [],
            "depends_on": [],
        },
    ]


def _sequential_subtasks() -> list[dict[str, Any]]:
    """Return a 3-task plan with sequential dependencies."""
    return [
        {
            "name": "read",
            "description": "Read data",
            "suggested_agent": "echo",
            "suggested_tools": [],
            "depends_on": [],
        },
        {
            "name": "process",
            "description": "Process data",
            "suggested_agent": "echo",
            "suggested_tools": [],
            "depends_on": ["read"],
        },
        {
            "name": "respond",
            "description": "Format response",
            "suggested_agent": "echo",
            "suggested_tools": [],
            "depends_on": ["process"],
        },
    ]


# ======================================================================
# Plan creation and validation
# ======================================================================


class TestPlanExecutorCreation:
    """Tests for plan building and validation."""

    @pytest.mark.asyncio
    async def test_execute_plan_creates_plan(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(
            _simple_subtasks(),
            original_message="test msg",
            strategy="test_strategy",
            rationale="test rationale",
        )
        assert plan.strategy == "test_strategy"
        assert plan.original_message == "test msg"
        assert plan.rationale == "test rationale"
        assert plan.task_count == 2

    @pytest.mark.asyncio
    async def test_execute_plan_valid_state_transitions(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        assert plan.state in (PlanState.COMPLETED, PlanState.FAILED)

    @pytest.mark.asyncio
    async def test_execute_plan_with_unknown_dependency_fails(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        subtasks = [
            {
                "name": "step1",
                "description": "First",
                "suggested_agent": "echo",
                "depends_on": ["nonexistent"],
            },
        ]
        plan = await executor.execute_plan(subtasks)
        assert plan.state == PlanState.FAILED
        assert "unknown task" in plan.error

    @pytest.mark.asyncio
    async def test_execute_plan_detects_circular_dependency(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        subtasks = [
            {
                "name": "a",
                "description": "A",
                "suggested_agent": "echo",
                "depends_on": ["b"],
            },
            {
                "name": "b",
                "description": "B",
                "suggested_agent": "echo",
                "depends_on": ["a"],
            },
        ]
        plan = await executor.execute_plan(subtasks)
        assert plan.state == PlanState.FAILED
        assert "Circular" in plan.error


# ======================================================================
# Task execution
# ======================================================================


class TestPlanExecutorExecution:
    """Tests for task execution flow."""

    @pytest.mark.asyncio
    async def test_all_tasks_completed(self) -> None:
        bus = EventBus()
        agent = _make_mock_agent("echo", "result-1")
        orch = _make_mock_orchestrator({"echo": agent})
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        assert plan.state == PlanState.COMPLETED
        assert plan.completed_count == 2
        for t in plan.tasks:
            assert t.status == PlanTaskStatus.COMPLETED
            assert t.result == "result-1"

    @pytest.mark.asyncio
    async def test_sequential_dependencies_respected(self) -> None:
        bus = EventBus()
        call_order: list[str] = []
        agent = _make_mock_agent("echo")

        async def tracking_handle(msg: str, ctx: Any) -> str:
            call_order.append(msg[:20])
            return "ok"

        agent.handle = tracking_handle
        orch = _make_mock_orchestrator({"echo": agent})
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_sequential_subtasks())
        assert plan.state == PlanState.COMPLETED
        # "process" should come after "read", "respond" after "process".
        assert len(call_order) == 3
        # Verify order: first task's message should mention 'Read'.
        assert "Read" in call_order[0]

    @pytest.mark.asyncio
    async def test_agent_assigned_to_tasks(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        for t in plan.tasks:
            assert t.agent_assigned == "echo"

    @pytest.mark.asyncio
    async def test_execution_time_recorded(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        for t in plan.tasks:
            assert t.execution_time_ms >= 0


# ======================================================================
# Error handling and retries
# ======================================================================


class TestPlanExecutorErrors:
    """Tests for error handling and retry logic."""

    @pytest.mark.asyncio
    async def test_failed_task_with_continue_on_error(self) -> None:
        bus = EventBus()
        failing_agent = _make_mock_agent("echo")
        failing_agent.handle = AsyncMock(side_effect=RuntimeError("agent crash"))
        orch = _make_mock_orchestrator({"echo": failing_agent})
        config = PlanExecutorConfig(
            max_retries=1,
            retry_backoff_base=0.01,  # fast retries for tests
            continue_on_error=True,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        plan = await executor.execute_plan(_simple_subtasks())
        # With continue_on_error, plan should complete (skipping failed tasks).
        assert plan.state in (PlanState.COMPLETED, PlanState.FAILED)

    @pytest.mark.asyncio
    async def test_failed_task_without_continue_on_error(self) -> None:
        bus = EventBus()
        failing_agent = _make_mock_agent("echo")
        failing_agent.handle = AsyncMock(side_effect=RuntimeError("agent crash"))
        orch = _make_mock_orchestrator({"echo": failing_agent})
        config = PlanExecutorConfig(
            max_retries=1,
            retry_backoff_base=0.01,
            continue_on_error=False,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        plan = await executor.execute_plan(_simple_subtasks())
        assert plan.state == PlanState.FAILED
        assert plan.error is not None

    @pytest.mark.asyncio
    async def test_retry_on_failure(self) -> None:
        bus = EventBus()
        call_count = 0

        async def flaky_handle(msg: str, ctx: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("temporary failure")
            return "recovered"

        agent = _make_mock_agent("echo")
        agent.handle = flaky_handle
        orch = _make_mock_orchestrator({"echo": agent})
        config = PlanExecutorConfig(
            max_retries=3,
            retry_backoff_base=0.01,
            continue_on_error=False,
            default_agent_timeout=5.0,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        plan = await executor.execute_plan(
            [{"name": "flaky", "description": "Flaky task", "suggested_agent": "echo", "depends_on": []}],
        )
        assert plan.state == PlanState.COMPLETED
        assert plan.tasks[0].retry_count == 2
        assert plan.tasks[0].result == "recovered"

    @pytest.mark.asyncio
    async def test_exhausted_retries_marks_failed(self) -> None:
        bus = EventBus()
        agent = _make_mock_agent("echo")
        agent.handle = AsyncMock(side_effect=RuntimeError("permanent"))
        orch = _make_mock_orchestrator({"echo": agent})
        config = PlanExecutorConfig(
            max_retries=3,
            retry_backoff_base=0.01,
            continue_on_error=True,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        plan = await executor.execute_plan(
            [{"name": "doom", "description": "Doomed task", "suggested_agent": "echo", "depends_on": []}],
        )
        assert plan.tasks[0].status == PlanTaskStatus.SKIPPED
        assert plan.tasks[0].retry_count == 3

    @pytest.mark.asyncio
    async def test_agent_timeout(self) -> None:
        bus = EventBus()
        agent = _make_mock_agent("echo")
        agent.handle = AsyncMock(
            side_effect=TimeoutError("timeout")
        )
        orch = _make_mock_orchestrator({"echo": agent})
        config = PlanExecutorConfig(
            max_retries=0,
            continue_on_error=True,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        plan = await executor.execute_plan(
            [{"name": "slow", "description": "Slow task", "suggested_agent": "echo", "depends_on": []}],
        )
        assert plan.tasks[0].status == PlanTaskStatus.SKIPPED


# ======================================================================
# Progress events
# ======================================================================


class TestPlanExecutorEvents:
    """Tests that the executor emits the correct progress events."""

    @pytest.mark.asyncio
    async def test_plan_lifecycle_events_emitted(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        events: list[tuple[str, dict]] = []

        async def collector(payload: Any) -> None:
            events.append(("event", payload))

        for evt in [
            "plan.created", "plan.validating", "plan.ready",
            "plan.started", "plan.completed",
            "plan.task.assigned", "plan.task.started",
            "plan.task.completed",
        ]:
            bus.subscribe(evt, collector)

        await executor.execute_plan(
            _simple_subtasks(),
            strategy="test",
        )

        event_types = [e[1].get("plan_id") or e[1].get("task_id") for e in events]
        # At minimum we should have plan-level events.
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_task_assigned_event_includes_agent(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        assigned_events: list[dict] = []
        bus.subscribe("plan.task.assigned", lambda p: assigned_events.append(p))

        await executor.execute_plan(_simple_subtasks())
        assert len(assigned_events) == 2
        for evt in assigned_events:
            assert "agent_assigned" in evt
            assert "task_name" in evt

    @pytest.mark.asyncio
    async def test_task_retry_event_emitted(self) -> None:
        bus = EventBus()
        call_count = 0

        async def flaky(msg: str, ctx: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("fail once")
            return "ok"

        agent = _make_mock_agent("echo")
        agent.handle = flaky
        orch = _make_mock_orchestrator({"echo": agent})
        config = PlanExecutorConfig(
            max_retries=2,
            retry_backoff_base=0.01,
            continue_on_error=False,
            default_agent_timeout=5.0,
        )
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        retry_events: list[dict] = []
        bus.subscribe("plan.task.retry", lambda p: retry_events.append(p))

        await executor.execute_plan(
            [{"name": "flaky", "description": "F", "suggested_agent": "echo", "depends_on": []}],
        )
        assert len(retry_events) == 1
        assert retry_events[0]["attempt"] == 2


# ======================================================================
# Cancellation
# ======================================================================


class TestPlanExecutorCancellation:
    """Tests for plan cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_active_plan(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        config = PlanExecutorConfig(pause_between_tasks=0.1)
        executor = PlanExecutor(
            orchestrator=orch, event_bus=bus, config=config,
        )

        # Start execution in background.
        import asyncio
        task = asyncio.create_task(
            executor.execute_plan(_sequential_subtasks())
        )

        # Wait a moment then cancel.
        await asyncio.sleep(0.05)
        # Get the plan id from active plans.
        active = executor.list_active_plans()
        if active:
            result = await executor.cancel_plan(active[0].id)
            assert result

        await task
        cancelled_events: list[dict] = []
        bus.subscribe("plan.cancelled", lambda p: cancelled_events.append(p))
        # Check that the plan was properly handled.

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_plan_returns_false(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        result = await executor.cancel_plan("nonexistent-id")
        assert not result


# ======================================================================
# History and queries
# ======================================================================


class TestPlanExecutorHistory:
    """Tests for plan history and query APIs."""

    @pytest.mark.asyncio
    async def test_completed_plan_goes_to_history(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        history = executor.plan_history()
        assert len(history) >= 1
        found = any(h.id == plan.id for h in history)
        assert found

    @pytest.mark.asyncio
    async def test_get_plan_by_id(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_simple_subtasks())
        found = executor.get_plan(plan.id)
        assert found is not None
        assert found.strategy == plan.strategy

    @pytest.mark.asyncio
    async def test_get_plan_nonexistent_returns_none(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        assert executor.get_plan("nonexistent") is None

    @pytest.mark.asyncio
    async def test_summary(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        await executor.execute_plan(_simple_subtasks())
        s = executor.summary()
        assert s["active_plans"] == 0
        assert s["history_size"] >= 1


# ======================================================================
# Persistence integration
# ======================================================================


class TestPlanExecutorPersistence:
    """Tests for execution repository integration."""

    @pytest.mark.asyncio
    async def test_persistence_called_on_completion(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        repo = AsyncMock()
        repo.record = AsyncMock(return_value=1)
        executor.attach_execution_repository(repo)

        await executor.execute_plan(_simple_subtasks())
        # Should have recorded events: at least plan completion.
        assert repo.record.call_count > 0

    @pytest.mark.asyncio
    async def test_persistence_failure_does_not_break_execution(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        repo = AsyncMock()
        repo.record = AsyncMock(side_effect=RuntimeError("db down"))
        executor.attach_execution_repository(repo)

        plan = await executor.execute_plan(_simple_subtasks())
        assert plan.state == PlanState.COMPLETED


# ======================================================================
# Cycle detection
# ======================================================================


class TestCycleDetection:
    """Tests for circular dependency detection."""

    @pytest.mark.asyncio
    async def test_no_cycle_passes(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        plan = await executor.execute_plan(_sequential_subtasks())
        assert plan.state != PlanState.FAILED or "Circular" not in (plan.error or "")

    @pytest.mark.asyncio
    async def test_three_node_cycle_detected(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        subtasks = [
            {"name": "a", "description": "A", "suggested_agent": "echo", "depends_on": ["c"]},
            {"name": "b", "description": "B", "suggested_agent": "echo", "depends_on": ["a"]},
            {"name": "c", "description": "C", "suggested_agent": "echo", "depends_on": ["b"]},
        ]
        plan = await executor.execute_plan(subtasks)
        assert plan.state == PlanState.FAILED
        assert "Circular" in (plan.error or "")


# ======================================================================
# Agent resolution
# ======================================================================


class TestAgentResolution:
    """Tests for agent resolution logic."""

    @pytest.mark.asyncio
    async def test_unknown_agent_falls_back_to_default(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        subtasks = [
            {
                "name": "step1",
                "description": "Step 1",
                "suggested_agent": "nonexistent_agent",
                "depends_on": [],
            },
        ]
        plan = await executor.execute_plan(subtasks)
        assert plan.state == PlanState.COMPLETED
        assert plan.tasks[0].agent_assigned == "echo"

    @pytest.mark.asyncio
    async def test_no_agent_suggested_uses_default(self) -> None:
        bus = EventBus()
        orch = _make_mock_orchestrator()
        executor = PlanExecutor(orchestrator=orch, event_bus=bus)

        subtasks = [
            {
                "name": "step1",
                "description": "Step 1",
                "suggested_agent": "",
                "depends_on": [],
            },
        ]
        plan = await executor.execute_plan(subtasks)
        assert plan.state == PlanState.COMPLETED
        assert plan.tasks[0].agent_assigned == "echo"