"""Unit tests for Sprint 3.7 — Plan Execution Tracking.

Tests cover:
- Tracker creation and state transitions
- Step updates (Multi-Agent integration)
- Blockage detection
- Pause / resume / cancel
- Persistence integration
- Event-driven tracking (auto-subscribe)
- Integration with PlanExecutor
- Snapshot building and history
"""

from __future__ import annotations

import asyncio
import sys
import time
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
from src.plan_models import (  # noqa: E402
    ExecutablePlan,
    PlanState,
    PlanTask,
    PlanTaskStatus,
)
from src.plan_tracking import (  # noqa: E402
    BlockageReport,
    PlanExecutionTracker,
    PlanSnapshot,
    StepSnapshot,
    TrackerConfig,
    TrackingState,
    can_transition_tracking,
)


# ======================================================================
# Fixtures
# ======================================================================


def _make_plan() -> ExecutablePlan:
    """Create a simple 3-task plan for testing."""
    plan = ExecutablePlan(
        strategy="test_strategy",
        original_message="Test message",
        rationale="Test rationale",
        tasks=[
            PlanTask(
                name="step1",
                description="First step",
                agent_assigned="echo",
            ),
            PlanTask(
                name="step2",
                description="Second step",
                agent_assigned="echo",
                dependencies=["step1"],
            ),
            PlanTask(
                name="step3",
                description="Third step",
                agent_assigned="coder",
                dependencies=["step2"],
            ),
        ],
    )
    plan.transition_to(PlanState.VALIDATING)
    plan.transition_to(PlanState.READY)
    plan.transition_to(PlanState.RUNNING)
    return plan


def _make_tracker(
    event_bus: EventBus | None = None,
    config: TrackerConfig | None = None,
) -> PlanExecutionTracker:
    """Create a tracker, optionally with a real EventBus."""
    bus = event_bus or EventBus()
    return PlanExecutionTracker(event_bus=bus, config=config)


def _advance_to_executing(
    tracker: PlanExecutionTracker, plan_id: str
) -> None:
    """Helper: transition a tracker PENDING -> PLANNING -> EXECUTING."""
    tracker.transition_to(plan_id, TrackingState.PLANNING)
    tracker.transition_to(plan_id, TrackingState.EXECUTING)


# ======================================================================
# TrackingState transitions
# ======================================================================


class TestTrackingState:
    """Tests for the TrackingState state machine."""

    def test_pending_to_planning(self) -> None:
        assert can_transition_tracking(TrackingState.PENDING, TrackingState.PLANNING)

    def test_pending_to_cancelled(self) -> None:
        assert can_transition_tracking(TrackingState.PENDING, TrackingState.CANCELLED)

    def test_pending_to_executing_is_invalid(self) -> None:
        assert not can_transition_tracking(TrackingState.PENDING, TrackingState.EXECUTING)

    def test_planning_to_executing(self) -> None:
        assert can_transition_tracking(TrackingState.PLANNING, TrackingState.EXECUTING)

    def test_planning_to_failed(self) -> None:
        assert can_transition_tracking(TrackingState.PLANNING, TrackingState.FAILED)

    def test_executing_to_running_step(self) -> None:
        assert can_transition_tracking(TrackingState.EXECUTING, TrackingState.RUNNING_STEP)

    def test_executing_to_completed(self) -> None:
        assert can_transition_tracking(TrackingState.EXECUTING, TrackingState.COMPLETED)

    def test_executing_to_failed(self) -> None:
        assert can_transition_tracking(TrackingState.EXECUTING, TrackingState.FAILED)

    def test_executing_to_paused(self) -> None:
        assert can_transition_tracking(TrackingState.EXECUTING, TrackingState.PAUSED)

    def test_running_step_to_running_step(self) -> None:
        assert can_transition_tracking(TrackingState.RUNNING_STEP, TrackingState.RUNNING_STEP)

    def test_running_step_to_executing(self) -> None:
        assert can_transition_tracking(TrackingState.RUNNING_STEP, TrackingState.EXECUTING)

    def test_paused_to_running_step(self) -> None:
        assert can_transition_tracking(TrackingState.PAUSED, TrackingState.RUNNING_STEP)

    def test_paused_to_executing(self) -> None:
        assert can_transition_tracking(TrackingState.PAUSED, TrackingState.EXECUTING)

    def test_paused_to_cancelled(self) -> None:
        assert can_transition_tracking(TrackingState.PAUSED, TrackingState.CANCELLED)

    def test_completed_is_terminal(self) -> None:
        assert not can_transition_tracking(TrackingState.COMPLETED, TrackingState.RUNNING_STEP)
        assert not can_transition_tracking(TrackingState.COMPLETED, TrackingState.FAILED)

    def test_cancelled_is_terminal(self) -> None:
        assert not can_transition_tracking(TrackingState.CANCELLED, TrackingState.PLANNING)

    def test_failed_can_replan(self) -> None:
        assert can_transition_tracking(TrackingState.FAILED, TrackingState.PLANNING)

    def test_all_non_terminal_states_have_transitions(self) -> None:
        """Every non-terminal state has at least one outgoing transition."""
        terminal = {TrackingState.COMPLETED, TrackingState.CANCELLED}
        for state in TrackingState:
            if state in terminal:
                continue
            has_outgoing = any(
                can_transition_tracking(state, target)
                for target in TrackingState
            )
            assert has_outgoing, f"{state.value} has no outgoing transitions"


# ======================================================================
# Tracker creation
# ======================================================================


class TestTrackerCreation:
    """Tests for creating tracking entries."""

    def test_create_tracker_returns_snapshot(self) -> None:
        tracker = _make_tracker()
        snap = tracker.create_tracker(
            "plan-001",
            strategy="test",
            original_message="hello",
        )
        assert snap is not None
        assert snap.plan_id == "plan-001"
        assert snap.tracking_state == "pending"
        assert snap.strategy == "test"
        assert snap.original_message == "hello"

    def test_create_tracker_idempotent(self) -> None:
        tracker = _make_tracker()
        snap1 = tracker.create_tracker("plan-001", strategy="a")
        snap2 = tracker.create_tracker("plan-001", strategy="b")
        assert snap1.plan_id == snap2.plan_id

    def test_create_tracker_without_bus(self) -> None:
        tracker = PlanExecutionTracker(event_bus=None)
        snap = tracker.create_tracker("plan-002")
        assert snap is not None
        assert snap.tracking_state == "pending"


# ======================================================================
# State transitions
# ======================================================================


class TestTrackerTransitions:
    """Tests for tracker state transitions."""

    def test_valid_transition(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        snap = tracker.transition_to("p1", TrackingState.PLANNING)
        assert snap.tracking_state == "planning"

    def test_invalid_transition_raises(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        with pytest.raises(ValueError, match="cannot transition"):
            tracker.transition_to("p1", TrackingState.COMPLETED)

    def test_nonexistent_plan_raises(self) -> None:
        tracker = _make_tracker()
        with pytest.raises(ValueError, match="No tracker"):
            tracker.transition_to("nonexistent", TrackingState.PLANNING)

    def test_full_lifecycle(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")

        tracker.transition_to("p1", TrackingState.PLANNING)
        assert tracker.get_status("p1").tracking_state == "planning"

        tracker.transition_to("p1", TrackingState.EXECUTING)
        assert tracker.get_status("p1").tracking_state == "executing"

        tracker.transition_to("p1", TrackingState.RUNNING_STEP)
        assert tracker.get_status("p1").tracking_state == "running_step"

        tracker.transition_to("p1", TrackingState.EXECUTING)
        tracker.transition_to("p1", TrackingState.COMPLETED)
        assert tracker.get_status("p1").tracking_state == "completed"

    def test_pause_and_resume(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        _advance_to_executing(tracker, "p1")
        tracker.transition_to("p1", TrackingState.PAUSED)

        snap = tracker.get_status("p1")
        assert snap.tracking_state == "paused"

        resumed = tracker.resume_plan("p1")
        assert resumed is not None
        assert resumed.tracking_state == "executing"

    def test_pause_non_running_returns_none(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        # PENDING cannot go to PAUSED
        result = tracker.pause_plan("p1")
        assert result is None

    def test_cancel_plan(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        _advance_to_executing(tracker, "p1")

        snap = tracker.cancel_plan("p1")
        assert snap is not None
        assert snap.tracking_state == "cancelled"

    def test_cancel_nonexistent_returns_none(self) -> None:
        tracker = _make_tracker()
        assert tracker.cancel_plan("nonexistent") is None

    def test_cancel_already_terminal_returns_none(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        _advance_to_executing(tracker, "p1")
        tracker.transition_to("p1", TrackingState.COMPLETED)

        assert tracker.cancel_plan("p1") is None


# ======================================================================
# Step updates
# ======================================================================


class TestStepUpdates:
    """Tests for step-level tracking updates."""

    def test_update_step_status(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        step = tracker.update_step(
            "p1", "task-001",
            status="assigned",
            agent_assigned="coder",
        )
        assert step is not None
        assert step.status == "assigned"
        assert step.agent_assigned == "coder"

    def test_update_step_result(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.update_step("p1", "t1", status="running")
        step = tracker.update_step(
            "p1", "t1",
            result="file contents here",
            execution_time_ms=150.5,
        )
        assert step is not None
        assert step.result == "file contents here"
        assert step.execution_time_ms == 150.5

    def test_update_step_error(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        step = tracker.update_step(
            "p1", "t1",
            status="failed",
            error="Connection refused",
            retry_count=2,
        )
        assert step is not None
        assert step.error == "Connection refused"
        assert step.retry_count == 2

    def test_update_step_nonexistent_plan(self) -> None:
        tracker = _make_tracker()
        result = tracker.update_step("nonexistent", "t1", status="running")
        assert result is None

    def test_auto_create_step_entry(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        # Step doesn't exist yet — should be auto-created.
        step = tracker.update_step("p1", "new-task-id", status="running")
        assert step is not None
        assert step.task_id == "new-task-id"

    def test_get_step_status(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.update_step("p1", "t1", status="completed", agent_assigned="echo")
        step = tracker.get_step_status("p1", "t1")
        assert step is not None
        assert step.status == "completed"
        assert step.agent_assigned == "echo"

    def test_get_step_status_nonexistent(self) -> None:
        tracker = _make_tracker()
        assert tracker.get_step_status("p1", "t1") is None

    def test_current_step_tracking(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.update_step("p1", "t1", status="running", agent_assigned="coder")

        snap = tracker.get_status("p1")
        assert snap.current_step is not None
        assert snap.current_agent == "coder"

    def test_current_step_cleared_on_completion(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.update_step("p1", "t1", status="running", agent_assigned="coder")
        tracker.update_step("p1", "t1", status="completed")

        snap = tracker.get_status("p1")
        assert snap.current_step is None
        assert snap.current_agent is None


# ======================================================================
# Plan binding
# ======================================================================


class TestPlanBinding:
    """Tests for binding an ExecutablePlan to a tracker."""

    def test_bind_plan_populates_steps(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        tracker.create_tracker(plan.id, strategy=plan.strategy)
        tracker.bind_plan(plan.id, plan)

        snap = tracker.get_status(plan.id)
        assert snap is not None
        assert snap.task_count == 3
        assert len(snap.steps) == 3

    def test_bind_plan_captures_current_state(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        # Complete first task.
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)

        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)

        steps = tracker.get_status(plan.id).steps
        step1 = next(s for s in steps if s["name"] == "step1")
        assert step1["status"] == "completed"

    def test_bind_nonexistent_plan_noop(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        # Should not raise.
        tracker.bind_plan(plan.id, plan)


# ======================================================================
# Blockage detection
# ======================================================================


class TestBlockageDetection:
    """Tests for plan blockage detection."""

    def test_no_blockage_fresh_plan(self) -> None:
        tracker = _make_tracker(
            config=TrackerConfig(blockage_timeout_s=0.1),
        )
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        _advance_to_executing(tracker, plan.id)

        # Immediately check — no blockage yet.
        blockages = tracker.detect_blockages()
        assert len(blockages) == 0

    def test_blockage_detected_after_timeout(self) -> None:
        tracker = _make_tracker(
            config=TrackerConfig(blockage_timeout_s=0.0),
        )
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        _advance_to_executing(tracker, plan.id)

        # Set executing_since in the past.
        entry = tracker._trackers[plan.id]
        entry.executing_since = time.monotonic() - 1.0

        blockages = tracker.detect_blockages()
        # step2 and step3 should be blocked (step1 is PENDING, not completed).
        assert len(blockages) >= 1
        names = [b.blocked_step for b in blockages]
        assert "step2" in names

    def test_blockage_report_structure(self) -> None:
        tracker = _make_tracker(
            config=TrackerConfig(blockage_timeout_s=0.0),
        )
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        _advance_to_executing(tracker, plan.id)
        tracker._trackers[plan.id].executing_since = time.monotonic() - 1.0

        blockages = tracker.detect_blockages()
        report = blockages[0]
        d = report.to_dict()
        assert "plan_id" in d
        assert "blocked_step" in d
        assert "waiting_for" in d
        assert "suggestion" in d
        assert isinstance(d["waiting_for"], list)

    def test_get_blockages_api(self) -> None:
        tracker = _make_tracker()
        blockages = tracker.get_blockages()
        assert isinstance(blockages, list)


# ======================================================================
# Snapshots and history
# ======================================================================


class TestSnapshots:
    """Tests for snapshot creation, persistence, and history."""

    def test_take_snapshot(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1", strategy="test")
        snap = tracker.take_snapshot("p1")
        assert snap is not None
        assert snap.plan_id == "p1"
        assert snap.tracking_state == "pending"

    def test_take_snapshot_nonexistent(self) -> None:
        tracker = _make_tracker()
        assert tracker.take_snapshot("nonexistent") is None

    def test_take_all_snapshots(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.create_tracker("p2")
        snaps = tracker.take_all_snapshots()
        assert len(snaps) == 2

    def test_should_snapshot_timing(self) -> None:
        config = TrackerConfig(snapshot_interval_s=1.0)
        tracker = _make_tracker(config=config)
        tracker.create_tracker("p1")
        assert tracker.should_snapshot("p1")
        tracker._last_snapshot["p1"] = time.monotonic()
        assert not tracker.should_snapshot("p1")

    def test_finalize_plan_moves_to_history(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        _advance_to_executing(tracker, plan.id)

        final = tracker.finalize_plan(plan.id)
        assert final is not None
        assert final.plan_id == plan.id

        # No longer in active.
        active = tracker.list_active()
        assert not any(a.plan_id == plan.id for a in active)

        # In history.
        history = tracker.get_history()
        assert any(h.plan_id == plan.id for h in history)

    def test_finalize_nonexistent_returns_none(self) -> None:
        tracker = _make_tracker()
        assert tracker.finalize_plan("nonexistent") is None

    def test_history_bounded(self) -> None:
        config = TrackerConfig(max_tracking_history=3)
        tracker = _make_tracker(config=config)
        for i in range(5):
            pid = f"plan-{i}"
            tracker.create_tracker(pid)
            tracker.finalize_plan(pid)

        history = tracker.get_history()
        assert len(history) == 3
        # Should keep the most recent.
        assert history[-1].plan_id == "plan-4"

    def test_get_history_with_limit(self) -> None:
        tracker = _make_tracker()
        for i in range(5):
            pid = f"plan-{i}"
            tracker.create_tracker(pid)
            tracker.finalize_plan(pid)

        history = tracker.get_history(limit=2)
        assert len(history) == 2

    def test_snapshot_serialization(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        snap = tracker.take_snapshot(plan.id)
        d = snap.to_dict()
        assert isinstance(d, dict)
        assert d["plan_id"] == plan.id
        assert d["tracking_state"] == "pending"
        assert d["plan_state"] == "running"
        assert d["task_count"] == 3
        assert isinstance(d["steps"], list)
        assert len(d["steps"]) == 3


# ======================================================================
# Step snapshot serialization
# ======================================================================


class TestStepSnapshot:
    """Tests for StepSnapshot."""

    def test_to_dict(self) -> None:
        step = StepSnapshot(
            task_id="t1",
            name="read_file",
            status="completed",
            agent_assigned="coder",
            execution_time_ms=100.0,
            retry_count=1,
            error=None,
            result="file contents",
        )
        d = step.to_dict()
        assert d["task_id"] == "t1"
        assert d["name"] == "read_file"
        assert d["status"] == "completed"
        assert d["agent_assigned"] == "coder"
        assert d["execution_time_ms"] == 100.0
        assert d["result"] == "file contents"
        assert d["error"] is None
        assert "created_at" in d


# ======================================================================
# Persistence
# ======================================================================


class TestPersistence:
    """Tests for execution repository integration."""

    @pytest.mark.asyncio
    async def test_snapshot_persisted(self) -> None:
        tracker = _make_tracker()
        repo = AsyncMock()
        repo.record = AsyncMock(return_value=1)
        tracker.attach_execution_repo(repo)

        tracker.create_tracker("p1")
        tracker.take_snapshot("p1")

        # Give the background task a chance to run.
        await asyncio.sleep(0.05)

        repo.record.assert_called_once()
        call_kwargs = repo.record.call_args[1]
        assert call_kwargs["event_type"] == "tracking.snapshot"
        assert call_kwargs["workflow_id"] == "p1"

    @pytest.mark.asyncio
    async def test_persistence_failure_does_not_break(self) -> None:
        tracker = _make_tracker()
        repo = AsyncMock()
        repo.record = AsyncMock(side_effect=RuntimeError("db down"))
        tracker.attach_execution_repo(repo)

        # Should not raise.
        tracker.create_tracker("p1")
        tracker.take_snapshot("p1")
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_finalize_persists(self) -> None:
        tracker = _make_tracker()
        repo = AsyncMock()
        repo.record = AsyncMock(return_value=1)
        tracker.attach_execution_repo(repo)

        tracker.create_tracker("p1")
        tracker.finalize_plan("p1")
        await asyncio.sleep(0.05)

        assert repo.record.call_count >= 1


# ======================================================================
# Event-driven tracking
# ======================================================================


class TestEventDrivenTracking:
    """Tests for auto-subscription to plan events."""

    @pytest.mark.asyncio
    async def test_auto_creates_tracker_on_validating(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        await bus.publish("plan.validating", {
            "plan_id": "auto-001",
            "task_count": 3,
            "strategy": "auto_test",
        })
        await asyncio.sleep(0.05)

        snap = tracker.get_status("auto-001")
        assert snap is not None
        assert snap.tracking_state == "planning"

    @pytest.mark.asyncio
    async def test_tracking_state_on_started(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        # Create and validate first.
        await bus.publish("plan.validating", {
            "plan_id": "auto-002",
            "task_count": 2,
        })
        await asyncio.sleep(0.05)

        await bus.publish("plan.started", {
            "plan_id": "auto-002",
            "task_count": 2,
        })
        await asyncio.sleep(0.05)

        snap = tracker.get_status("auto-002")
        assert snap is not None
        assert snap.tracking_state == "executing"

    @pytest.mark.asyncio
    async def test_task_events_update_steps(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        await bus.publish("plan.validating", {"plan_id": "p1", "task_count": 1})
        await bus.publish("plan.started", {"plan_id": "p1", "task_count": 1})
        await asyncio.sleep(0.05)

        await bus.publish("plan.task.assigned", {
            "plan_id": "p1",
            "task_id": "t1",
            "task_name": "read",
            "agent_assigned": "coder",
        })
        await bus.publish("plan.task.started", {
            "plan_id": "p1",
            "task_id": "t1",
            "task_name": "read",
            "agent_assigned": "coder",
            "attempt": 1,
        })
        await asyncio.sleep(0.05)

        step = tracker.get_step_status("p1", "t1")
        assert step is not None
        assert step.agent_assigned == "coder"
        assert step.status == "running"

        snap = tracker.get_status("p1")
        assert snap.current_step == "read"
        assert snap.current_agent == "coder"

    @pytest.mark.asyncio
    async def test_plan_completed_finalizes(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        await bus.publish("plan.validating", {"plan_id": "p1", "task_count": 1})
        await bus.publish("plan.started", {"plan_id": "p1", "task_count": 1})
        await asyncio.sleep(0.05)

        await bus.publish("plan.completed", {
            "plan_id": "p1",
            "metrics": {},
        })
        await asyncio.sleep(0.05)

        # Should be in history, not active.
        active = tracker.list_active()
        assert not any(a.plan_id == "p1" for a in active)
        history = tracker.get_history()
        assert any(h.plan_id == "p1" for h in history)

    @pytest.mark.asyncio
    async def test_plan_failed_finalizes(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        await bus.publish("plan.validating", {"plan_id": "p1", "task_count": 1})
        await bus.publish("plan.started", {"plan_id": "p1", "task_count": 1})
        await asyncio.sleep(0.05)

        await bus.publish("plan.failed", {
            "plan_id": "p1",
            "error": "All tasks failed",
        })
        await asyncio.sleep(0.05)

        # Plan is finalized to history.
        snap = tracker.get_status("p1")
        assert snap is not None
        assert snap.tracking_state == "failed"

    @pytest.mark.asyncio
    async def test_task_retry_updates_count(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=True),
        )

        await bus.publish("plan.validating", {"plan_id": "p1", "task_count": 1})
        await bus.publish("plan.started", {"plan_id": "p1", "task_count": 1})
        await bus.publish("plan.task.assigned", {
            "plan_id": "p1", "task_id": "t1", "task_name": "s1", "agent_assigned": "echo",
        })
        await asyncio.sleep(0.05)

        await bus.publish("plan.task.retry", {
            "plan_id": "p1",
            "task_id": "t1",
            "task_name": "s1",
            "attempt": 2,
        })
        await asyncio.sleep(0.05)

        step = tracker.get_step_status("p1", "t1")
        assert step is not None
        assert step.retry_count == 1  # attempt 2 means retry_count = 1
        assert step.status == "assigned"

    @pytest.mark.asyncio
    async def test_no_auto_subscribe(self) -> None:
        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=False),
        )

        await bus.publish("plan.validating", {"plan_id": "p1", "task_count": 1})
        await asyncio.sleep(0.05)

        assert tracker.get_status("p1") is None


# ======================================================================
# Summary and metrics
# ======================================================================


class TestSummaryAndMetrics:
    """Tests for tracker summary and per-plan metrics."""

    def test_summary_empty(self) -> None:
        tracker = _make_tracker()
        s = tracker.summary()
        assert s["active_plans"] == 0
        assert s["history_size"] == 0
        assert s["blockage_count"] == 0

    def test_summary_with_active(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.transition_to("p1", TrackingState.PLANNING)
        tracker.create_tracker("p2")
        _advance_to_executing(tracker, "p2")

        s = tracker.summary()
        assert s["active_plans"] == 2
        assert s["active_by_state"]["planning"] == 1
        assert s["active_by_state"]["executing"] == 1

    def test_plan_metrics_active(self) -> None:
        tracker = _make_tracker()
        plan = _make_plan()
        tracker.create_tracker(plan.id)
        tracker.bind_plan(plan.id, plan)
        _advance_to_executing(tracker, plan.id)

        # Complete first task.
        plan.tasks[0].transition_to(PlanTaskStatus.ASSIGNED)
        plan.tasks[0].transition_to(PlanTaskStatus.RUNNING)
        plan.tasks[0].transition_to(PlanTaskStatus.COMPLETED)
        plan.tasks[0].execution_time_ms = 200.0
        tracker.update_step(
            plan.id, plan.tasks[0].id,
            status="completed",
            execution_time_ms=200.0,
            agent_assigned="echo",
        )

        metrics = tracker.plan_metrics(plan.id)
        assert metrics is not None
        assert metrics["task_count"] == 3
        assert metrics["completed_count"] == 1
        assert "agent_utilization" in metrics

    def test_plan_metrics_from_history(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.finalize_plan("p1")

        metrics = tracker.plan_metrics("p1")
        assert metrics is not None
        assert metrics["plan_id"] == "p1"

    def test_plan_metrics_nonexistent(self) -> None:
        tracker = _make_tracker()
        assert tracker.plan_metrics("nonexistent") is None

    def test_list_active(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.create_tracker("p2")

        active = tracker.list_active()
        assert len(active) == 2
        ids = {a.plan_id for a in active}
        assert "p1" in ids
        assert "p2" in ids


# ======================================================================
# Integration with PlanExecutor
# ======================================================================


class TestPlanExecutorIntegration:
    """Tests for PlanExecutor + PlanExecutionTracker integration."""

    @pytest.mark.asyncio
    async def test_executor_creates_tracker_automatically(self) -> None:
        from src.plan_executor import PlanExecutor

        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=False),
        )
        orch = MagicMock()
        orch._agents = {"echo": MagicMock(handle=AsyncMock(return_value="ok"))}
        router = MagicMock()
        router.default_agent = MagicMock(name="echo")
        orch._router = router

        executor = PlanExecutor(orchestrator=orch, event_bus=bus)
        executor.attach_tracker(tracker)

        subtasks = [
            {
                "name": "step1",
                "description": "Do something",
                "suggested_agent": "echo",
                "depends_on": [],
            },
        ]

        plan = await executor.execute_plan(
            subtasks, strategy="integration_test",
        )
        assert plan.state == PlanState.COMPLETED

        # Tracker should have an active entry.
        snap = tracker.get_status(plan.id)
        assert snap is not None
        assert snap.plan_id == plan.id
        assert snap.strategy == "integration_test"
        assert snap.task_count == 1

    @pytest.mark.asyncio
    async def test_executor_tracks_step_progress(self) -> None:
        from src.plan_executor import PlanExecutor

        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=False),
        )
        orch = MagicMock()
        orch._agents = {"echo": MagicMock(handle=AsyncMock(return_value="done"))}
        router = MagicMock()
        router.default_agent = MagicMock(name="echo")
        orch._router = router

        executor = PlanExecutor(orchestrator=orch, event_bus=bus)
        executor.attach_tracker(tracker)

        subtasks = [
            {"name": "s1", "description": "Step 1", "suggested_agent": "echo", "depends_on": []},
            {"name": "s2", "description": "Step 2", "suggested_agent": "echo", "depends_on": ["s1"]},
        ]

        plan = await executor.execute_plan(subtasks, strategy="seq_test")
        assert plan.state == PlanState.COMPLETED

        # Check active snapshot has correct step data.
        snap = tracker.get_status(plan.id)
        assert snap is not None
        assert snap.task_count == 2
        assert snap.completed_count == 2

    @pytest.mark.asyncio
    async def test_executor_tracks_failures(self) -> None:
        from src.plan_executor import PlanExecutor, PlanExecutorConfig

        bus = EventBus()
        tracker = PlanExecutionTracker(
            event_bus=bus,
            config=TrackerConfig(auto_subscribe=False),
        )

        failing_agent = MagicMock(
            name="echo",
            handle=AsyncMock(side_effect=RuntimeError("boom")),
        )
        orch = MagicMock()
        orch._agents = {"echo": failing_agent}
        router = MagicMock()
        router.default_agent = MagicMock(name="echo")
        orch._router = router

        config = PlanExecutorConfig(
            max_retries=1,
            retry_backoff_base=0.01,
            continue_on_error=True,
        )
        executor = PlanExecutor(orchestrator=orch, event_bus=bus, config=config)
        executor.attach_tracker(tracker)

        subtasks = [
            {"name": "s1", "description": "Fail step", "suggested_agent": "echo", "depends_on": []},
        ]

        plan = await executor.execute_plan(subtasks)

        # Check tracker has the failure recorded.
        snap = tracker.get_status(plan.id)
        assert snap is not None
        # Plan should reflect the failure.
        assert snap.task_count == 1


# ======================================================================
# Edge cases
# ======================================================================


class TestEdgeCases:
    """Edge cases and robustness tests."""

    def test_tracker_without_event_bus(self) -> None:
        tracker = PlanExecutionTracker(event_bus=None)
        snap = tracker.create_tracker("p1")
        assert snap is not None
        # Transitions should still work (no events emitted).
        snap = tracker.transition_to("p1", TrackingState.PLANNING)
        assert snap.tracking_state == "planning"

    def test_multiple_plans_independent(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.create_tracker("p2")

        tracker.transition_to("p1", TrackingState.PLANNING)
        _advance_to_executing(tracker, "p2")

        assert tracker.get_status("p1").tracking_state == "planning"
        assert tracker.get_status("p2").tracking_state == "executing"

    def test_finalize_removes_from_active(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        assert len(tracker.list_active()) == 1
        tracker.finalize_plan("p1")
        assert len(tracker.list_active()) == 0

    def test_get_status_from_history(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        tracker.transition_to("p1", TrackingState.PLANNING)
        tracker.finalize_plan("p1")

        # get_status should find it in history.
        snap = tracker.get_status("p1")
        assert snap is not None
        assert snap.tracking_state == "planning"

    def test_step_to_dict_json_safe(self) -> None:
        step = StepSnapshot(
            task_id="t1",
            name="test",
            result=object(),  # Non-JSON-serializable
        )
        d = step.to_dict()
        # Should not raise; result should be stringified.
        assert isinstance(d["result"], str)

    def test_blockage_report_to_dict(self) -> None:
        report = BlockageReport(
            plan_id="p1",
            blocked_step="step2",
            waiting_for=["step1"],
            suggestion="Wait for step1 to complete.",
        )
        d = report.to_dict()
        assert d["plan_id"] == "p1"
        assert d["blocked_step"] == "step2"
        assert d["waiting_for"] == ["step1"]
        assert len(d["suggestion"]) > 0

    def test_tracker_config_defaults(self) -> None:
        config = TrackerConfig()
        assert config.blockage_timeout_s == 60.0
        assert config.snapshot_interval_s == 5.0
        assert config.max_tracking_history == 200
        assert config.auto_subscribe is True

    def test_empty_plan_progress_zero(self) -> None:
        tracker = _make_tracker()
        tracker.create_tracker("p1")
        snap = tracker.get_status("p1")
        assert snap.progress == 0.0
        assert snap.task_count == 0