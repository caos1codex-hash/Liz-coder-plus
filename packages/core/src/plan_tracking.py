"""Plan Execution Tracking — Sprint 3.7.

Provides an advanced tracking layer that sits between the Planner,
PlanExecutor, Orchestrator and Multi-Agent System.  The
``PlanExecutionTracker`` maintains a real-time view of every plan's
progress, detects blockages, and synchronises state across components.

Architecture::

    User message
      -> Planner.plan()                [PLANNING]
      -> PlanExecutor.execute_plan()   [EXECUTING]
        -> step 1 assigned             [RUNNING_STEP]
        -> step 1 completed
        -> step 2 assigned             [RUNNING_STEP]
        -> ...
      -> plan completed                [COMPLETED]

The tracker emits ``tracking.*`` events so that UI, logging, and
auditing subscribers can react in real time.  Snapshots are
periodically (or on demand) persisted via the ``ExecutionRepository``
so that plans survive restarts.

Key classes:

    TrackingState   — high-level lifecycle enum for the tracker.
    StepSnapshot    — point-in-time capture of a single step's state.
    PlanSnapshot    — point-in-time capture of a full plan's state.
    BlockageReport  — structured description of a detected blockage.
    PlanExecutionTracker — the main service.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

from src.events import (
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_PAUSED,
    PLAN_STARTED,
    PLAN_TASK_ASSIGNED,
    PLAN_TASK_COMPLETED,
    PLAN_TASK_FAILED,
    PLAN_TASK_RETRY,
    PLAN_TASK_SKIPPED,
    PLAN_TASK_STARTED,
    PLAN_VALIDATING,
    TRACKING_PLAN_BLOCKED,
    TRACKING_PLAN_EXECUTING,
    TRACKING_PLAN_PENDING,
    TRACKING_PLAN_PLANNING,
    TRACKING_PLAN_RESUMED,
    TRACKING_PLAN_RUNNING_STEP,
    TRACKING_STEP_PROGRESS,
)
from src.plan_models import (
    ExecutablePlan,
    PlanTaskStatus,
)

if TYPE_CHECKING:
    from src.event_bus import EventBus

logger = logging.getLogger(__name__)


# ======================================================================
# Tracking state machine
# ======================================================================


class TrackingState(str, Enum):
    """High-level lifecycle states for the PlanExecutionTracker.

    These states are a superset of ``PlanState`` and provide the
    additional granularity requested in Sprint 3.7.

    Transitions::

        PENDING -> PLANNING -> EXECUTING -> RUNNING_STEP
                                            -> COMPLETED
                                            -> FAILED
                                            -> PAUSED -> RUNNING_STEP / CANCELLED
                                            -> CANCELLED
    """

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    RUNNING_STEP = "running_step"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


_ALLOWED_TRACKING_TRANSITIONS: dict[TrackingState, frozenset[TrackingState]] = {
    TrackingState.PENDING: frozenset({TrackingState.PLANNING, TrackingState.CANCELLED}),
    TrackingState.PLANNING: frozenset({TrackingState.EXECUTING, TrackingState.FAILED, TrackingState.CANCELLED}),
    TrackingState.EXECUTING: frozenset({
        TrackingState.RUNNING_STEP, TrackingState.COMPLETED,
        TrackingState.FAILED, TrackingState.PAUSED, TrackingState.CANCELLED,
    }),
    TrackingState.RUNNING_STEP: frozenset({
        TrackingState.RUNNING_STEP, TrackingState.EXECUTING,
        TrackingState.COMPLETED, TrackingState.FAILED,
        TrackingState.PAUSED, TrackingState.CANCELLED,
    }),
    TrackingState.PAUSED: frozenset({
        TrackingState.RUNNING_STEP, TrackingState.EXECUTING,
        TrackingState.CANCELLED,
    }),
    TrackingState.COMPLETED: frozenset(),   # terminal
    TrackingState.FAILED: frozenset({TrackingState.PLANNING}),  # can re-plan
    TrackingState.CANCELLED: frozenset(),   # terminal
}


def can_transition_tracking(
    current: TrackingState, target: TrackingState
) -> bool:
    """Return True if a tracker can transition from *current* to *target*."""
    return target in _ALLOWED_TRACKING_TRANSITIONS.get(current, frozenset())


# ======================================================================
# Snapshot models
# ======================================================================


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _json_safe(value: Any) -> Any:
    """Make a value JSON-serialisable."""
    if value is None:
        return None
    try:
        import json
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


@dataclass
class StepSnapshot:
    """Point-in-time capture of a single step's tracking state.

    Attributes:
        task_id:            UUID of the underlying PlanTask.
        name:               Step name.
        status:             Current PlanTaskStatus (raw string).
        agent_assigned:     Agent executing this step.
        execution_time_ms:  Wall-clock time of the last execution attempt.
        retry_count:        Number of retries so far.
        max_retries:        Maximum allowed retries.
        error:              Last error message (if any).
        result:             Last result (JSON-safe).
        started_at:         ISO timestamp when the step started running.
        completed_at:       ISO timestamp when the step reached a terminal state.
        created_at:         ISO timestamp when the snapshot was taken.
    """

    task_id: str
    name: str
    status: str = "pending"
    agent_assigned: str = ""
    execution_time_ms: float = 0.0
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    result: Any = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status,
            "agent_assigned": self.agent_assigned,
            "execution_time_ms": self.execution_time_ms,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self.error,
            "result": _json_safe(self.result),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
        }


@dataclass
class PlanSnapshot:
    """Point-in-time capture of a full plan's tracking state.

    Attributes:
        plan_id:            UUID of the underlying ExecutablePlan.
        tracking_state:     Current TrackingState (raw string).
        plan_state:         Current PlanState (raw string).
        strategy:           Planning strategy label.
        original_message:   User message that triggered this plan.
        progress:           0.0..1.0 completion ratio.
        task_count:         Total number of steps.
        completed_count:    Number of completed steps.
        failed_count:       Number of failed steps.
        skipped_count:      Number of skipped steps.
        current_step:       Name of the step currently running (if any).
        current_agent:      Agent executing the current step (if any).
        total_duration_ms:  Sum of all step durations so far.
        total_retries:      Sum of all step retries so far.
        error:              Plan-level error (if any).
        steps:              List of StepSnapshot dicts.
        created_at:         ISO timestamp of the snapshot.
    """

    plan_id: str
    tracking_state: str = "pending"
    plan_state: str = "created"
    strategy: str = ""
    original_message: str = ""
    progress: float = 0.0
    task_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    current_step: str | None = None
    current_agent: str | None = None
    total_duration_ms: float = 0.0
    total_retries: int = 0
    error: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "tracking_state": self.tracking_state,
            "plan_state": self.plan_state,
            "strategy": self.strategy,
            "original_message": self.original_message,
            "progress": round(self.progress, 4),
            "task_count": self.task_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "current_step": self.current_step,
            "current_agent": self.current_agent,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "total_retries": self.total_retries,
            "error": self.error,
            "steps": list(self.steps),
            "created_at": self.created_at,
        }


@dataclass
class BlockageReport:
    """Structured description of a detected blockage.

    Attributes:
        plan_id:            The blocked plan's ID.
        blocked_step:       Name of the step that is blocked.
        waiting_for:        Names of dependencies that have not completed.
        waiting_since:      ISO timestamp when the blockage was detected.
        suggestion:         Human-readable suggestion for resolution.
    """

    plan_id: str
    blocked_step: str
    waiting_for: list[str] = field(default_factory=list)
    waiting_since: str = field(default_factory=_now_iso)
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "blocked_step": self.blocked_step,
            "waiting_for": list(self.waiting_for),
            "waiting_since": self.waiting_since,
            "suggestion": self.suggestion,
        }


# ======================================================================
# PlanExecutionTracker
# ======================================================================


@dataclass
class TrackerConfig:
    """Configuration for the PlanExecutionTracker.

    Attributes:
        blockage_timeout_s:     Seconds a step can wait for dependencies
                                before being flagged as blocked.
        snapshot_interval_s:    Minimum seconds between automatic snapshots.
        max_tracking_history:   Maximum number of tracked plans kept in RAM.
        auto_subscribe:         If True, automatically subscribe to plan
                                execution events on the event bus.
    """

    blockage_timeout_s: float = 60.0
    snapshot_interval_s: float = 5.0
    max_tracking_history: int = 200
    auto_subscribe: bool = True


class PlanExecutionTracker:
    """Advanced plan execution tracking service.

    The tracker maintains a real-time view of all active and recently
    completed plans.  It listens to plan execution events, updates
    internal state, detects blockages, and periodically persists
    snapshots to the execution repository.

    The tracker is designed to be attached to the Orchestrator
    alongside the PlanExecutor, providing a higher-level view that
    the UI and other consumers can query.

    Usage::

        tracker = PlanExecutionTracker(event_bus=bus)
        tracker.attach_execution_repo(execution_repo)

        # The tracker subscribes to plan events automatically.
        # Query state at any time:
        snapshot = tracker.get_status(plan_id)
        blockages = tracker.detect_blockages()

    Args:
        event_bus:    Optional EventBus for subscribing to plan events.
        config:       Tracker configuration.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        config: TrackerConfig | None = None,
    ) -> None:
        self._config = config or TrackerConfig()
        self._bus = event_bus

        # Active tracking state keyed by plan_id.
        self._trackers: dict[str, _PlanTrackerEntry] = {}

        # Completed tracking history (bounded).
        self._history: list[PlanSnapshot] = []
        self._blockage_reports: list[BlockageReport] = []

        # Execution repository for persistence (late binding).
        self._execution_repo: Any = None

        # Timestamp of the last automatic snapshot (per plan).
        self._last_snapshot: dict[str, float] = {}

        # Auto-subscribe to plan events.
        if self._bus is not None and self._config.auto_subscribe:
            self._subscribe_events()

        logger.info("PlanExecutionTracker initialized")

    # ------------------------------------------------------------------
    # Public API — Lifecycle
    # ------------------------------------------------------------------

    def attach_execution_repo(self, repo: Any) -> None:
        """Attach an ExecutionRepository for snapshot persistence."""
        self._execution_repo = repo
        logger.info("ExecutionRepository attached to PlanExecutionTracker")

    # ------------------------------------------------------------------
    # Public API — Tracking creation
    # ------------------------------------------------------------------

    def create_tracker(
        self,
        plan_id: str,
        strategy: str = "",
        original_message: str = "",
    ) -> PlanSnapshot:
        """Create a new tracking entry for a plan.

        This is typically called right before the Planner starts
        generating the plan.  The tracker starts in PENDING state.

        Args:
            plan_id:         UUID of the plan to track.
            strategy:        Planning strategy label.
            original_message: User message that triggered the plan.

        Returns:
            The initial PlanSnapshot.
        """
        if plan_id in self._trackers:
            logger.warning("Tracker already exists for plan '%s'", plan_id[:8])
            return self.get_status(plan_id)

        entry = _PlanTrackerEntry(
            plan_id=plan_id,
            tracking_state=TrackingState.PENDING,
            strategy=strategy,
            original_message=original_message,
        )
        self._trackers[plan_id] = entry

        logger.info(
            "Tracker created for plan '%s' (strategy=%s)",
            plan_id[:8], strategy,
        )

        # Emit pending event.
        asyncio.get_event_loop().create_task(
            self._emit(TRACKING_PLAN_PENDING, {
                "plan_id": plan_id,
                "strategy": strategy,
            })
        )

        return self._build_snapshot(entry)

    # ------------------------------------------------------------------
    # Public API — State transitions
    # ------------------------------------------------------------------

    def transition_to(
        self, plan_id: str, new_state: TrackingState
    ) -> PlanSnapshot:
        """Transition a tracked plan to a new tracking state.

        Args:
            plan_id:   UUID of the plan.
            new_state: Target TrackingState.

        Returns:
            Updated PlanSnapshot.

        Raises:
            ValueError: If the plan is not tracked or the transition
                        is invalid.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            raise ValueError(
                f"No tracker for plan '{plan_id[:8]}'; "
                f"use create_tracker() first"
            )

        if not can_transition_tracking(entry.tracking_state, new_state):
            raise ValueError(
                f"Plan '{plan_id[:8]}' cannot transition from "
                f"{entry.tracking_state.value} to {new_state.value}"
            )

        old = entry.tracking_state
        entry.tracking_state = new_state
        entry.updated_at = _now_iso()

        logger.info(
            "Plan '%s' tracking %s -> %s",
            plan_id[:8], old.value, new_state.value,
        )

        # Map to plan-level event.
        event_map = {
            TrackingState.PLANNING: TRACKING_PLAN_PLANNING,
            TrackingState.EXECUTING: TRACKING_PLAN_EXECUTING,
            TrackingState.RUNNING_STEP: TRACKING_PLAN_RUNNING_STEP,
            TrackingState.PAUSED: PLAN_PAUSED,
            TrackingState.CANCELLED: PLAN_CANCELLED,
            TrackingState.COMPLETED: PLAN_COMPLETED,
            TrackingState.FAILED: PLAN_FAILED,
        }
        evt_type = event_map.get(new_state)
        if evt_type:
            asyncio.get_event_loop().create_task(
                self._emit(evt_type, {
                    "plan_id": plan_id,
                    "from_state": old.value,
                    "to_state": new_state.value,
                })
            )

        return self._build_snapshot(entry)

    # ------------------------------------------------------------------
    # Public API — Step updates (Multi-Agent integration)
    # ------------------------------------------------------------------

    def update_step(
        self,
        plan_id: str,
        task_id: str,
        *,
        status: str | None = None,
        agent_assigned: str | None = None,
        result: Any = None,
        error: str | None = None,
        execution_time_ms: float | None = None,
        retry_count: int | None = None,
    ) -> StepSnapshot | None:
        """Update the tracking state of a single step.

        Called by the PlanExecutor or Multi-Agent System when a step
        changes state.

        Args:
            plan_id:           UUID of the parent plan.
            task_id:           UUID of the step (PlanTask).
            status:            New PlanTaskStatus value (string).
            agent_assigned:    Agent executing the step.
            result:            Step result.
            error:             Step error message.
            execution_time_ms: Step execution duration.
            retry_count:       Updated retry count.

        Returns:
            The updated StepSnapshot, or None if the plan is not tracked.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            logger.debug(
                "update_step: plan '%s' not tracked", plan_id[:8]
            )
            return None

        step = entry.steps.get(task_id)
        if step is None:
            # Auto-create step entry from available info.
            step = StepSnapshot(task_id=task_id, name=task_id[:12])
            entry.steps[task_id] = step

        if status is not None:
            step.status = status
        if agent_assigned is not None:
            step.agent_assigned = agent_assigned
        if result is not None:
            step.result = result
        if error is not None:
            step.error = error
        if execution_time_ms is not None:
            step.execution_time_ms = execution_time_ms
        if retry_count is not None:
            step.retry_count = retry_count

        # Update plan-level current step.
        if status == "running":
            entry.current_step = step.name
            entry.current_agent = step.agent_assigned
        if status in ("completed", "failed", "skipped"):
            if entry.current_step == step.name:
                entry.current_step = None
                entry.current_agent = None

        entry.updated_at = _now_iso()

        # Emit step progress event.
        asyncio.get_event_loop().create_task(
            self._emit(TRACKING_STEP_PROGRESS, {
                "plan_id": plan_id,
                "task_id": task_id,
                "task_name": step.name,
                "status": step.status,
                "agent_assigned": step.agent_assigned,
            })
        )

        return step

    def bind_plan(self, plan_id: str, plan: ExecutablePlan) -> None:
        """Bind a full ExecutablePlan to an existing tracker entry.

        This synchronises the tracker with the plan's current state
        and populates all step entries from the plan's tasks.

        Args:
            plan_id: UUID of the plan.
            plan:    The ExecutablePlan instance.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            logger.warning(
                "bind_plan: no tracker for plan '%s'", plan_id[:8]
            )
            return

        entry.plan = plan
        entry.strategy = plan.strategy
        entry.original_message = plan.original_message

        # Populate step entries from plan tasks.
        for task in plan.tasks:
            step = StepSnapshot(
                task_id=task.id,
                name=task.name,
                status=task.status.value,
                agent_assigned=task.agent_assigned,
                execution_time_ms=task.execution_time_ms,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                error=task.error,
                result=task.result,
                started_at=task.started_at,
                completed_at=task.completed_at,
            )
            entry.steps[task.id] = step

        # Update current step.
        running = [t for t in plan.tasks if t.status == PlanTaskStatus.RUNNING]
        if running:
            entry.current_step = running[0].name
            entry.current_agent = running[0].agent_assigned

        entry.updated_at = _now_iso()
        logger.info(
            "Plan '%s' bound to tracker (%d steps)",
            plan_id[:8], len(plan.tasks),
        )

    # ------------------------------------------------------------------
    # Public API — Queries
    # ------------------------------------------------------------------

    def get_status(self, plan_id: str) -> PlanSnapshot | None:
        """Return the current tracking snapshot for a plan.

        If the plan is not actively tracked, checks history.

        Args:
            plan_id: UUID of the plan.

        Returns:
            PlanSnapshot or None.
        """
        entry = self._trackers.get(plan_id)
        if entry is not None:
            return self._build_snapshot(entry)

        # Check history.
        for snap in self._history:
            if snap.plan_id == plan_id:
                return snap
        return None

    def get_step_status(
        self, plan_id: str, task_id: str
    ) -> StepSnapshot | None:
        """Return the current step snapshot.

        Args:
            plan_id: UUID of the plan.
            task_id: UUID of the step.

        Returns:
            StepSnapshot or None.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            return None
        return entry.steps.get(task_id)

    def list_active(self) -> list[PlanSnapshot]:
        """Return snapshots of all actively tracked plans."""
        return [
            self._build_snapshot(entry)
            for entry in self._trackers.values()
        ]

    def get_history(self, limit: int = 50) -> list[PlanSnapshot]:
        """Return the most recent completed/failed plan snapshots."""
        return list(self._history[-limit:])

    def get_blockages(self) -> list[BlockageReport]:
        """Return all currently detected blockages."""
        return list(self._blockage_reports)

    # ------------------------------------------------------------------
    # Public API — Control
    # ------------------------------------------------------------------

    def pause_plan(self, plan_id: str) -> PlanSnapshot | None:
        """Pause a tracked plan.

        Returns the updated snapshot, or None if the plan is not
        tracked or cannot be paused.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            return None
        try:
            return self.transition_to(plan_id, TrackingState.PAUSED)
        except ValueError:
            return None

    def resume_plan(self, plan_id: str) -> PlanSnapshot | None:
        """Resume a paused plan.

        Transitions PAUSED -> EXECUTING.

        Returns the updated snapshot, or None if the plan is not
        tracked or cannot be resumed.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            return None
        if entry.tracking_state != TrackingState.PAUSED:
            return None

        entry.tracking_state = TrackingState.EXECUTING
        entry.updated_at = _now_iso()
        entry.paused_duration = (
            (time.monotonic() - entry.paused_at)
            if entry.paused_at else 0.0
        )
        entry.paused_at = None

        logger.info(
            "Plan '%s' resumed (was paused for %.1fs)",
            plan_id[:8], entry.paused_duration,
        )

        asyncio.get_event_loop().create_task(
            self._emit(TRACKING_PLAN_RESUMED, {
                "plan_id": plan_id,
                "paused_duration_s": round(entry.paused_duration, 2),
            })
        )

        return self._build_snapshot(entry)

    def cancel_plan(self, plan_id: str) -> PlanSnapshot | None:
        """Cancel a tracked plan.

        Returns the updated snapshot, or None if the plan is not
        tracked or is already terminal.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            return None
        try:
            return self.transition_to(plan_id, TrackingState.CANCELLED)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Blockage detection
    # ------------------------------------------------------------------

    def detect_blockages(self) -> list[BlockageReport]:
        """Scan all active plans for blocked steps.

        A step is considered blocked when:
        - It is in PENDING or ASSIGNED state.
        - At least one of its dependencies has not completed.
        - The plan has been in EXECUTING or RUNNING_STEP state for
          longer than ``blockage_timeout_s``.

        Returns:
            List of BlockageReport instances.
        """
        self._blockage_reports.clear()
        now = time.monotonic()

        for entry in self._trackers.values():
            if entry.tracking_state not in (
                TrackingState.EXECUTING,
                TrackingState.RUNNING_STEP,
            ):
                continue

            if entry.plan is None:
                continue

            executing_since = entry.executing_since or now
            elapsed = now - executing_since

            if elapsed < self._config.blockage_timeout_s:
                continue

            for task in entry.plan.tasks:
                if task.status not in (
                    PlanTaskStatus.PENDING,
                    PlanTaskStatus.ASSIGNED,
                ):
                    continue

                # Check if any dependency is not completed/skipped.
                completed_names: set[str] = set()
                for t in entry.plan.tasks:
                    if t.status in (
                        PlanTaskStatus.COMPLETED,
                        PlanTaskStatus.SKIPPED,
                    ):
                        completed_names.add(t.name)

                unmet = [
                    dep for dep in task.dependencies
                    if dep not in completed_names
                ]

                if unmet:
                    report = BlockageReport(
                        plan_id=entry.plan_id,
                        blocked_step=task.name,
                        waiting_for=unmet,
                        waiting_since=_now_iso(),
                        suggestion=(
                            f"Step '{task.name}' is waiting for "
                            f"{', '.join(unmet)} which have not "
                            f"completed after {elapsed:.0f}s. "
                            f"Consider cancelling the plan or "
                            f"skipping the blocked step."
                        ),
                    )
                    self._blockage_reports.append(report)

                    asyncio.get_event_loop().create_task(
                        self._emit(TRACKING_PLAN_BLOCKED, {
                            "plan_id": entry.plan_id,
                            "blocked_step": task.name,
                            "waiting_for": unmet,
                            "elapsed_s": round(elapsed, 2),
                        })
                    )

        return list(self._blockage_reports)

    # ------------------------------------------------------------------
    # Snapshots and persistence
    # ------------------------------------------------------------------

    def take_snapshot(self, plan_id: str) -> PlanSnapshot | None:
        """Take and persist a snapshot of a tracked plan.

        Returns the snapshot, or None if the plan is not tracked.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            return None

        snapshot = self._build_snapshot(entry)

        # Persist if repo is attached.
        if self._execution_repo is not None:
            asyncio.get_event_loop().create_task(
                self._persist_snapshot(snapshot)
            )

        self._last_snapshot[plan_id] = time.monotonic()
        return snapshot

    def take_all_snapshots(self) -> list[PlanSnapshot]:
        """Take snapshots of all actively tracked plans."""
        results: list[PlanSnapshot] = []
        for plan_id in list(self._trackers.keys()):
            snap = self.take_snapshot(plan_id)
            if snap is not None:
                results.append(snap)
        return results

    def should_snapshot(self, plan_id: str) -> bool:
        """Check if enough time has passed for an automatic snapshot.

        Args:
            plan_id: UUID of the plan.

        Returns:
            True if a snapshot should be taken.
        """
        last = self._last_snapshot.get(plan_id, 0.0)
        return (time.monotonic() - last) >= self._config.snapshot_interval_s

    # ------------------------------------------------------------------
    # Plan finalisation
    # ------------------------------------------------------------------

    def finalize_plan(self, plan_id: str) -> PlanSnapshot | None:
        """Move a tracked plan to history.

        Called when the underlying ExecutablePlan reaches a terminal
        state.  The tracker entry is removed and a final snapshot
        is archived.

        Returns:
            The final PlanSnapshot, or None.
        """
        entry = self._trackers.pop(plan_id, None)
        if entry is None:
            return None

        snapshot = self._build_snapshot(entry)
        self._history.append(snapshot)
        self._last_snapshot.pop(plan_id, None)

        # Trim history.
        if len(self._history) > self._config.max_tracking_history:
            overflow = (
                len(self._history) - self._config.max_tracking_history
            )
            del self._history[:overflow]

        # Persist final snapshot.
        if self._execution_repo is not None:
            asyncio.get_event_loop().create_task(
                self._persist_snapshot(snapshot)
            )

        logger.info(
            "Plan '%s' finalized in tracking history "
            "(state=%s, history_size=%d)",
            plan_id[:8], entry.tracking_state.value,
            len(self._history),
        )

        return snapshot

    # ------------------------------------------------------------------
    # Summary / metrics
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return aggregate tracking statistics."""
        state_counts: dict[str, int] = {}
        for entry in self._trackers.values():
            s = entry.tracking_state.value
            state_counts[s] = state_counts.get(s, 0) + 1

        return {
            "active_plans": len(self._trackers),
            "active_by_state": state_counts,
            "history_size": len(self._history),
            "blockage_count": len(self._blockage_reports),
        }

    def plan_metrics(self, plan_id: str) -> dict[str, Any] | None:
        """Return detailed tracking metrics for a specific plan.

        Includes step-level timing, agent utilisation, and retry
        statistics.
        """
        entry = self._trackers.get(plan_id)
        if entry is None:
            # Check history.
            for snap in self._history:
                if snap.plan_id == plan_id:
                    return snap.to_dict()
            return None

        snap = self._build_snapshot(entry)

        # Add per-agent timing breakdown.
        agent_time: dict[str, float] = {}
        agent_tasks: dict[str, int] = {}
        for step in entry.steps.values():
            agent = step.agent_assigned or "unknown"
            agent_time[agent] = (
                agent_time.get(agent, 0.0) + step.execution_time_ms
            )
            agent_tasks[agent] = agent_tasks.get(agent, 0) + 1

        return {
            **snap.to_dict(),
            "agent_utilization": {
                agent: {
                    "total_time_ms": round(t, 2),
                    "tasks": agent_tasks.get(agent, 0),
                }
                for agent, t in agent_time.items()
            },
        }

    # ------------------------------------------------------------------
    # Event subscription (internal)
    # ------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        """Subscribe to plan execution events on the EventBus."""
        if self._bus is None:
            return

        handlers = {
            PLAN_VALIDATING: self._on_plan_validating,
            PLAN_STARTED: self._on_plan_started,
            PLAN_PAUSED: self._on_plan_paused,
            PLAN_COMPLETED: self._on_plan_completed,
            PLAN_FAILED: self._on_plan_failed,
            PLAN_CANCELLED: self._on_plan_cancelled,
            PLAN_TASK_ASSIGNED: self._on_task_assigned,
            PLAN_TASK_STARTED: self._on_task_started,
            PLAN_TASK_COMPLETED: self._on_task_completed,
            PLAN_TASK_FAILED: self._on_task_failed,
            PLAN_TASK_SKIPPED: self._on_task_skipped,
            PLAN_TASK_RETRY: self._on_task_retry,
        }

        for event_type, handler in handlers.items():
            self._bus.subscribe(event_type, handler)

        logger.info("Subscribed to %d plan event types", len(handlers))

    # -- Plan-level event handlers --

    async def _on_plan_validating(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        if plan_id not in self._trackers:
            self.create_tracker(
                plan_id,
                strategy=payload.get("strategy", ""),
            )
        self.transition_to(plan_id, TrackingState.PLANNING)

    async def _on_plan_started(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        if plan_id not in self._trackers:
            self.create_tracker(plan_id)
        self.transition_to(plan_id, TrackingState.EXECUTING)
        # Record executing_since.
        entry = self._trackers.get(plan_id)
        if entry is not None:
            entry.executing_since = time.monotonic()

    async def _on_plan_paused(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        entry = self._trackers.get(plan_id)
        if entry is not None:
            entry.tracking_state = TrackingState.PAUSED
            entry.paused_at = time.monotonic()
            entry.updated_at = _now_iso()

    async def _on_plan_completed(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        entry = self._trackers.get(plan_id)
        if entry is not None:
            entry.tracking_state = TrackingState.COMPLETED
            entry.updated_at = _now_iso()
        self.finalize_plan(plan_id)

    async def _on_plan_failed(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        entry = self._trackers.get(plan_id)
        if entry is not None:
            entry.tracking_state = TrackingState.FAILED
            entry.error = payload.get("error")
            entry.updated_at = _now_iso()
        self.finalize_plan(plan_id)

    async def _on_plan_cancelled(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        entry = self._trackers.get(plan_id)
        if entry is not None:
            entry.tracking_state = TrackingState.CANCELLED
            entry.error = payload.get("error")
            entry.updated_at = _now_iso()
        self.finalize_plan(plan_id)

    # -- Task-level event handlers --

    async def _on_task_assigned(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        agent = payload.get("agent_assigned", "")
        name = payload.get("task_name", "")
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is None:
            step = StepSnapshot(task_id=task_id, name=name)
            entry.steps[task_id] = step
        step.agent_assigned = agent
        step.status = "assigned"
        entry.updated_at = _now_iso()

    async def _on_task_started(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        name = payload.get("task_name", "")
        agent = payload.get("agent_assigned", "")
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is None:
            step = StepSnapshot(task_id=task_id, name=name)
            entry.steps[task_id] = step
        step.status = "running"
        step.agent_assigned = agent
        step.started_at = step.started_at or _now_iso()
        entry.current_step = name
        entry.current_agent = agent
        # Transition to RUNNING_STEP.
        if entry.tracking_state == TrackingState.EXECUTING:
            entry.tracking_state = TrackingState.RUNNING_STEP
        entry.updated_at = _now_iso()

    async def _on_task_completed(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        duration = payload.get("duration_ms", 0.0)
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is not None:
            step.status = "completed"
            step.execution_time_ms = duration
            step.completed_at = _now_iso()
            step.error = None
        if entry.current_step and entry.steps.get(task_id):
            if entry.steps[task_id].name == entry.current_step:
                entry.current_step = None
                entry.current_agent = None
        # If no more running steps, go back to EXECUTING.
        if entry.tracking_state == TrackingState.RUNNING_STEP:
            has_running = any(
                s.status == "running" for s in entry.steps.values()
            )
            if not has_running:
                entry.tracking_state = TrackingState.EXECUTING
        entry.updated_at = _now_iso()

    async def _on_task_failed(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        error = payload.get("error", "")
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is not None:
            step.status = "failed"
            step.error = error
            step.completed_at = _now_iso()
        if entry.current_step and entry.steps.get(task_id):
            if entry.steps[task_id].name == entry.current_step:
                entry.current_step = None
                entry.current_agent = None
        entry.updated_at = _now_iso()

    async def _on_task_skipped(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is not None:
            step.status = "skipped"
            step.completed_at = _now_iso()
        entry.updated_at = _now_iso()

    async def _on_task_retry(self, payload: Any) -> None:
        plan_id = payload.get("plan_id", "")
        task_id = payload.get("task_id", "")
        attempt = payload.get("attempt", 0)
        entry = self._trackers.get(plan_id)
        if entry is None:
            return
        step = entry.steps.get(task_id)
        if step is not None:
            step.retry_count = attempt - 1
            step.status = "assigned"
        entry.updated_at = _now_iso()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_snapshot(self, snapshot: PlanSnapshot) -> None:
        """Persist a tracking snapshot to the execution repository."""
        if self._execution_repo is None:
            return
        try:
            await self._execution_repo.record(
                event_type="tracking.snapshot",
                workflow_id=snapshot.plan_id,
                status=snapshot.tracking_state,
                result=snapshot.to_dict(),
                metadata={
                    "plan_state": snapshot.plan_state,
                    "strategy": snapshot.strategy,
                    "progress": snapshot.progress,
                    "task_count": snapshot.task_count,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to persist snapshot for plan '%s'",
                snapshot.plan_id[:8],
            )

    # ------------------------------------------------------------------
    # Snapshot building
    # ------------------------------------------------------------------

    def _build_snapshot(self, entry: _PlanTrackerEntry) -> PlanSnapshot:
        """Build a PlanSnapshot from an internal tracker entry."""
        plan = entry.plan

        task_count = 0
        completed_count = 0
        failed_count = 0
        skipped_count = 0
        total_duration = 0.0
        total_retries = 0
        plan_state_str = "created"
        progress = 0.0
        error: str | None = entry.error

        if plan is not None:
            task_count = plan.task_count
            completed_count = plan.completed_count
            failed_count = plan.failed_count
            skipped_count = plan.skipped_count
            total_duration = sum(t.execution_time_ms for t in plan.tasks)
            total_retries = sum(t.retry_count for t in plan.tasks)
            plan_state_str = plan.state.value
            progress = plan.progress
            if plan.error is not None:
                error = plan.error
        else:
            # Derive from step entries.
            steps = list(entry.steps.values())
            task_count = len(steps)
            for s in steps:
                if s.status == "completed":
                    completed_count += 1
                elif s.status == "failed":
                    failed_count += 1
                elif s.status == "skipped":
                    skipped_count += 1
                total_duration += s.execution_time_ms
                total_retries += s.retry_count
            if task_count > 0:
                progress = (completed_count + skipped_count) / task_count

        return PlanSnapshot(
            plan_id=entry.plan_id,
            tracking_state=entry.tracking_state.value,
            plan_state=plan_state_str,
            strategy=entry.strategy,
            original_message=entry.original_message,
            progress=progress,
            task_count=task_count,
            completed_count=completed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            current_step=entry.current_step,
            current_agent=entry.current_agent,
            total_duration_ms=total_duration,
            total_retries=total_retries,
            error=error,
            steps=[s.to_dict() for s in entry.steps.values()],
        )

    # ------------------------------------------------------------------
    # Event helper
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, payload: Any) -> None:
        """Emit an event, isolating failures."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


# ======================================================================
# Internal tracker entry
# ======================================================================


class _PlanTrackerEntry:
    """Internal tracking state for a single plan.

    Not part of the public API.  Used by PlanExecutionTracker
    to maintain per-plan state between events.
    """

    __slots__ = (
        "plan_id",
        "tracking_state",
        "strategy",
        "original_message",
        "plan",
        "steps",
        "current_step",
        "current_agent",
        "error",
        "created_at",
        "updated_at",
        "executing_since",
        "paused_at",
        "paused_duration",
    )

    def __init__(
        self,
        plan_id: str,
        tracking_state: TrackingState = TrackingState.PENDING,
        strategy: str = "",
        original_message: str = "",
    ) -> None:
        self.plan_id: str = plan_id
        self.tracking_state: TrackingState = tracking_state
        self.strategy: str = strategy
        self.original_message: str = original_message
        self.plan: ExecutablePlan | None = None
        self.steps: dict[str, StepSnapshot] = {}
        self.current_step: str | None = None
        self.current_agent: str | None = None
        self.error: str | None = None
        now = _now_iso()
        self.created_at: str = now
        self.updated_at: str = now
        self.executing_since: float | None = None
        self.paused_at: float | None = None
        self.paused_duration: float = 0.0


__all__ = [
    "BlockageReport",
    "PlanExecutionTracker",
    "PlanSnapshot",
    "StepSnapshot",
    "TrackerConfig",
    "TrackingState",
    "can_transition_tracking",
]