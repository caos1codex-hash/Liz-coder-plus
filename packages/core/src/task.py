"""Task system for the Orchestrator.

Sprint 1.7 — Phase 3.

A ``Task`` is the unit of work the orchestrator can address, pause,
resume, cancel and observe. Tasks are produced by:

  - User messages (one task per inbound message).
  - The Planner (one task per sub-task in a plan).
  - The Workflow engine (one task per node in a workflow DAG).

The ``TaskManager`` owns the lifecycle of all tasks in the system and
exposes a query API for the orchestrator and observability layer.

Lifecycle::

    PENDING → READY → RUNNING ─┬─→ COMPLETED
                  ↑             ├─→ FAILED
                  │             ├─→ CANCELLED
                  └─ WAITING ───┘
                              (paused / blocked)

Transitions are guarded: a CANCELLED task cannot be resumed; a
COMPLETED task cannot transition back; etc.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from src.events import (
    TASK_CANCELLED,
    TASK_COMPLETED,
    TASK_CREATED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_RESUMED,
    TASK_STARTED,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Task state machine
# ----------------------------------------------------------------------


class TaskState(str, Enum):
    """Lifecycle states of a Task.

    The string value is used as the canonical state identifier in
    events and JSON serializations.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """Priority levels for scheduling.

    LOWER priority value = higher scheduling precedence
    (mirrors Unix ``nice`` semantics).
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


_PRIORITY_WEIGHT: dict[TaskPriority, int] = {
    TaskPriority.CRITICAL: 0,
    TaskPriority.HIGH: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.LOW: 3,
}


# Allowed transitions: from_state -> set of to_states
_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({
        TaskState.READY, TaskState.CANCELLED, TaskState.FAILED,
    }),
    TaskState.READY: frozenset({
        TaskState.RUNNING, TaskState.CANCELLED, TaskState.WAITING,
    }),
    TaskState.RUNNING: frozenset({
        TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED,
        TaskState.WAITING,
    }),
    TaskState.WAITING: frozenset({
        TaskState.READY, TaskState.CANCELLED,
    }),
    TaskState.COMPLETED: frozenset(),  # terminal
    TaskState.FAILED: frozenset({TaskState.READY}),  # can retry
    TaskState.CANCELLED: frozenset(),  # terminal
}


# ----------------------------------------------------------------------
# Task data model
# ----------------------------------------------------------------------


@dataclass
class Task:
    """A single unit of work managed by the orchestrator.

    Attributes:
        id:           Unique identifier (UUID4).
        name:         Short human-readable name.
        description:  Longer description of what the task does.
        priority:     Scheduling priority.
        state:        Current lifecycle state.
        progress:     0..100, hint for UIs.
        agent_name:   Name of the agent that should handle the task.
        tools_used:   List of tool names invoked during execution.
        created_at:   UTC timestamp.
        started_at:   UTC timestamp (None until started).
        completed_at: UTC timestamp (None until terminal).
        result:       Output of the task (any).
        errors:       List of error messages recorded during execution.
        metadata:     Free-form metadata bag.
        parent_id:    ID of the parent task (for sub-tasks).
        workflow_id:  ID of the owning workflow (if any).
    """

    name: str
    description: str = ""
    priority: TaskPriority = TaskPriority.NORMAL
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: TaskState = TaskState.PENDING
    progress: int = 0
    agent_name: str = ""
    tools_used: list[str] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any = None
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    workflow_id: str | None = None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Whether the task is in a terminal state."""
        return self.state in (
            TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED
        )

    def can_transition_to(self, new_state: TaskState) -> bool:
        """Check if a state transition is allowed."""
        return new_state in _ALLOWED_TRANSITIONS.get(self.state, frozenset())

    def transition_to(self, new_state: TaskState) -> None:
        """Transition to a new state.

        Raises:
            ValueError: If the transition is not allowed.
        """
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"Task '{self.name}' cannot transition from "
                f"{self.state.value} to {new_state.value}"
            )
        old_state = self.state
        self.state = new_state

        # Update timestamps.
        if new_state == TaskState.RUNNING and self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        if new_state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self.completed_at = datetime.now(timezone.utc)

        logger.debug(
            "Task '%s' (%s) %s → %s",
            self.name, self.id, old_state.value, new_state.value,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the task to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority.value,
            "state": self.state.value,
            "progress": self.progress,
            "agent_name": self.agent_name,
            "tools_used": list(self.tools_used),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result if _is_jsonable(self.result) else str(self.result),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
            "parent_id": self.parent_id,
            "workflow_id": self.workflow_id,
        }


def _is_jsonable(value: Any) -> bool:
    """Best-effort check that a value is JSON-serializable."""
    try:
        import json
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


# ----------------------------------------------------------------------
# Task Manager
# ----------------------------------------------------------------------


# Type for the optional async callback invoked when a task transitions.
TaskCallback = Callable[[Task], Awaitable[None]]


class TaskManager:
    """Owns the lifecycle of all ``Task`` instances in the system.

    Responsibilities:
        - Create tasks.
        - Track state transitions (with validation).
        - Cancel running tasks.
        - Pause / resume tasks (via WAITING ↔ READY).
        - Query by id, state, agent, workflow.
        - Maintain a bounded history (LRU) of terminal tasks.
        - Emit lifecycle events through the EventBus.

    The manager is intentionally storage-only: it does not execute
    tasks. Execution is delegated to the Orchestrator (for the agent)
    or the WorkflowManager (for DAG-ordered tasks).
    """

    # Maximum number of terminal tasks kept in history.
    MAX_HISTORY = 200

    def __init__(self, event_bus: Any | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        self._history: list[Task] = []
        self._bus = event_bus

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def event_bus(self) -> Any | None:
        return self._bus

    @property
    def count(self) -> int:
        """Number of active (non-terminal) tasks."""
        return sum(1 for t in self._tasks.values() if not t.is_terminal())

    @property
    def total(self) -> int:
        """Number of tasks currently tracked (active + recently terminal)."""
        return len(self._tasks)

    # ------------------------------------------------------------------
    # Create / cancel / pause / resume
    # ------------------------------------------------------------------

    async def create(
        self,
        name: str,
        *,
        description: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        agent_name: str = "",
        parent_id: str | None = None,
        workflow_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Create a new task in the PENDING state.

        Emits ``task.created``.
        """
        task = Task(
            name=name,
            description=description,
            priority=priority,
            agent_name=agent_name,
            parent_id=parent_id,
            workflow_id=workflow_id,
            metadata=metadata or {},
        )
        self._tasks[task.id] = task
        await self._emit(TASK_CREATED, {
            "task_id": task.id,
            "name": task.name,
            "priority": task.priority.value,
            "agent_name": task.agent_name,
            "workflow_id": task.workflow_id,
        })
        logger.info(
            "Task created: '%s' (%s) agent=%s",
            task.name, task.id, task.agent_name,
        )
        return task

    async def start(self, task_id: str) -> Task:
        """Mark a task as READY then RUNNING (single-step).

        For tasks created via the pipeline, READY is implicit.
        """
        task = self._require(task_id)
        if task.state == TaskState.PENDING:
            task.transition_to(TaskState.READY)
        task.transition_to(TaskState.RUNNING)
        await self._emit(TASK_STARTED, {
            "task_id": task.id,
            "name": task.name,
            "agent_name": task.agent_name,
        })
        return task

    async def complete(
        self, task_id: str, result: Any = None, *, tools_used: list[str] | None = None
    ) -> Task:
        """Mark a task as COMPLETED.

        Emits ``task.completed``.
        """
        task = self._require(task_id)
        if tools_used:
            task.tools_used.extend(tools_used)
        task.result = result
        task.progress = 100
        task.transition_to(TaskState.COMPLETED)
        await self._emit(TASK_COMPLETED, {
            "task_id": task.id,
            "name": task.name,
            "agent_name": task.agent_name,
            "tools_used": list(task.tools_used),
        })
        self._archive(task)
        return task

    async def fail(self, task_id: str, error: str) -> Task:
        """Mark a task as FAILED with an error message.

        Emits ``task.failed``.
        """
        task = self._require(task_id)
        task.errors.append(error)
        task.transition_to(TaskState.FAILED)
        await self._emit(TASK_FAILED, {
            "task_id": task.id,
            "name": task.name,
            "agent_name": task.agent_name,
            "error": error,
        })
        self._archive(task)
        return task

    async def cancel(self, task_id: str, reason: str = "") -> Task:
        """Cancel a task.

        Terminal tasks cannot be cancelled.

        Emits ``task.cancelled``.
        """
        task = self._require(task_id)
        if task.is_terminal():
            raise ValueError(
                f"Task '{task.name}' is already terminal ({task.state.value})"
            )
        if reason:
            task.errors.append(f"cancelled: {reason}")
        task.transition_to(TaskState.CANCELLED)
        await self._emit(TASK_CANCELLED, {
            "task_id": task.id,
            "name": task.name,
            "reason": reason,
        })
        self._archive(task)
        return task

    async def pause(self, task_id: str) -> Task:
        """Pause a running task (move to WAITING).

        Emits ``task.paused``.
        """
        task = self._require(task_id)
        if task.state != TaskState.RUNNING:
            raise ValueError(
                f"Task '{task.name}' can only be paused from RUNNING "
                f"(current: {task.state.value})"
            )
        task.transition_to(TaskState.WAITING)
        await self._emit(TASK_PAUSED, {
            "task_id": task.id,
            "name": task.name,
        })
        return task

    async def resume(self, task_id: str) -> Task:
        """Resume a paused task (WAITING → READY).

        Emits ``task.resumed``.
        """
        task = self._require(task_id)
        if task.state != TaskState.WAITING:
            raise ValueError(
                f"Task '{task.name}' can only be resumed from WAITING "
                f"(current: {task.state.value})"
            )
        task.transition_to(TaskState.READY)
        await self._emit(TASK_RESUMED, {
            "task_id": task.id,
            "name": task.name,
        })
        return task

    async def update_progress(self, task_id: str, progress: int) -> Task:
        """Update the progress hint (0..100). Does not change state."""
        task = self._require(task_id)
        task.progress = max(0, min(100, int(progress)))
        return task

    async def retry(self, task_id: str) -> Task:
        """Move a FAILED task back to READY for a new attempt.

        Emits ``task.resumed`` with ``reason='retry'``.
        """
        task = self._require(task_id)
        if task.state != TaskState.FAILED:
            raise ValueError(
                f"Task '{task.name}' can only be retried from FAILED "
                f"(current: {task.state.value})"
            )
        task.transition_to(TaskState.READY)
        await self._emit(TASK_RESUMED, {
            "task_id": task.id,
            "name": task.name,
            "reason": "retry",
        })
        return task

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        """Look up a task by id."""
        return self._tasks.get(task_id)

    def list_active(self) -> list[Task]:
        """Return all non-terminal tasks."""
        return [t for t in self._tasks.values() if not t.is_terminal()]

    def list_by_state(self, state: TaskState) -> list[Task]:
        """Return all tasks in the given state."""
        return [t for t in self._tasks.values() if t.state == state]

    def list_by_agent(self, agent_name: str) -> list[Task]:
        """Return all tasks assigned to the given agent."""
        return [t for t in self._tasks.values() if t.agent_name == agent_name]

    def list_by_workflow(self, workflow_id: str) -> list[Task]:
        """Return all tasks belonging to a workflow."""
        return [t for t in self._tasks.values() if t.workflow_id == workflow_id]

    def history(self, limit: int = 50) -> list[Task]:
        """Return the most recent terminal tasks."""
        return list(self._history[-limit:])

    def summary(self) -> dict[str, Any]:
        """Return aggregate stats for observability."""
        counts: dict[str, int] = {s.value: 0 for s in TaskState}
        for t in self._tasks.values():
            counts[t.state.value] += 1
        return {
            "total": len(self._tasks),
            "active": self.count,
            "history_size": len(self._history),
            "by_state": counts,
        }

    # ------------------------------------------------------------------
    # Scheduling helpers
    # ------------------------------------------------------------------

    def next_ready(self) -> Task | None:
        """Return the highest-priority READY task, or None.

        Ties broken by creation time (FIFO).
        """
        ready = [t for t in self._tasks.values() if t.state == TaskState.READY]
        if not ready:
            return None
        ready.sort(
            key=lambda t: (_PRIORITY_WEIGHT[t.priority], t.created_at)
        )
        return ready[0]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")
        return task

    def _archive(self, task: Task) -> None:
        """Move a terminal task to history, evicting the oldest if needed."""
        self._history.append(task)
        if len(self._history) > self.MAX_HISTORY:
            overflow = len(self._history) - self.MAX_HISTORY
            del self._history[:overflow]

    async def _emit(self, event_type: str, payload: Any) -> None:
        """Emit an event if a bus is attached."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)


__all__ = [
    "Task",
    "TaskCallback",
    "TaskManager",
    "TaskPriority",
    "TaskState",
]
