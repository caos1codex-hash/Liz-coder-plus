"""Task model for the planner (Sprint 2.8).

A :class:`Task` is the atomic unit of work in an
:class:`~planner.execution_plan.ExecutionPlan`. It carries:

- Identity (``id``, ``title``, ``description``).
- Lifecycle state (``status``, ``priority``).
- Dependency graph metadata (``dependencies``, ``required_capabilities``).
- Resource estimates (``estimated_duration``, ``estimated_tokens``,
  ``estimated_cost``).
- Assignment (``assigned_agent``).
- Free-form metadata (``metadata``).
- Audit timestamps (``created_at``, ``updated_at``).

State transitions are validated against
:func:`~planner.models.can_transition` to keep the lifecycle consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .exceptions import InvalidTaskError
from .models import TaskPriority, TaskStatus, can_transition
from .utils import merge_dicts, new_task_id, now_iso

# ----------------------------------------------------------------------
# Field defaults
# ----------------------------------------------------------------------

_DEFAULT_PRIORITY = TaskPriority.NORMAL
_DEFAULT_STATUS = TaskStatus.PENDING


# ----------------------------------------------------------------------
# Task
# ----------------------------------------------------------------------


@dataclass
class Task:
    """Atomic unit of work in an execution plan.

    The dataclass is mutable (so the scheduler can update ``status``
    and ``assigned_agent`` in place), but every mutation goes through
    a state-validating helper (``mark_running`` / ``mark_completed`` /
    ``mark_failed`` / ``mark_waiting`` / ``cancel`` / ``skip`` /
    ``reset``) so that the lifecycle stays consistent.

    Example::

        >>> t = Task(title="Write README", description="Add docs")
        >>> t.status
        <TaskStatus.PENDING: 'pending'>
        >>> t.mark_running()
        >>> t.status
        <TaskStatus.RUNNING: 'running'>
        >>> t.mark_completed()
        >>> t.status
        <TaskStatus.COMPLETED: 'completed'>
    """

    # Identity
    id: str = field(default_factory=lambda: new_task_id("task"))
    title: str = ""
    description: str = ""

    # Lifecycle
    status: TaskStatus = _DEFAULT_STATUS
    priority: TaskPriority = _DEFAULT_PRIORITY

    # Dependencies & capabilities
    dependencies: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)

    # Assignment
    assigned_agent: str | None = None

    # Estimates (filled by CostEstimator)
    estimated_duration: float = 0.0  # seconds
    estimated_tokens: int = 0  # LLM tokens (prompt + completion)
    estimated_cost: float = 0.0  # USD

    # Free-form metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    # Audit
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # Internal retry counter (not serialised by default unless
    # ``include_retry_count`` is True).
    retry_count: int = 0
    max_retries: int = 3

    # ------------------------------------------------------------------
    # __post_init__
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        if not self.title and not self.description:
            # Allow empty title only when description is provided and
            # vice-versa. This catches programmer errors early without
            # being overly strict (a task with just an id is valid for
            # tests).
            if not self.title and not self.description:
                # Default the title to the id if both are empty so that
                # serialisation always produces something readable.
                self.title = self.id
        # Defensive copies so external list mutations don't leak in.
        self.dependencies = list(self.dependencies)
        self.required_capabilities = list(self.required_capabilities)
        self.metadata = dict(self.metadata)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _transition(self, target: TaskStatus, *, reason: str = "") -> None:
        """Validate and apply a state transition.

        Raises:
            InvalidTaskError: if the transition is not allowed.
        """
        if not can_transition(self.status, target):
            raise InvalidTaskError(
                self.id,
                f"cannot transition from {self.status.value} to {target.value}"
                + (f": {reason}" if reason else ""),
            )
        self.status = target
        self.updated_at = now_iso()

    def mark_ready(self) -> None:
        """PENDING -> READY."""
        self._transition(TaskStatus.READY)

    def mark_running(self) -> None:
        """READY/RWAITING -> RUNNING."""
        # Allow READY or WAITING to transition to RUNNING directly.
        if self.status == TaskStatus.WAITING:
            # WAITING -> RUNNING is allowed.
            self._transition(TaskStatus.RUNNING)
        else:
            self._transition(TaskStatus.RUNNING)

    def mark_waiting(self) -> None:
        """RUNNING -> WAITING."""
        self._transition(TaskStatus.WAITING)

    def mark_completed(self) -> None:
        """RUNNING/WAITING -> COMPLETED."""
        self._transition(TaskStatus.COMPLETED)

    def mark_failed(self, *, error: str = "") -> None:
        """RUNNING/WAITING/READY -> FAILED.

        The error string (if any) is recorded in ``metadata['last_error']``.
        """
        if error:
            self.metadata["last_error"] = error
        self._transition(TaskStatus.FAILED, reason=error)

    def cancel(self) -> None:
        """Any active state -> CANCELLED."""
        if self.status == TaskStatus.CANCELLED:
            return
        # Allow cancelling from any non-terminal state.
        if TaskStatus.is_terminal(self.status):
            raise InvalidTaskError(
                self.id,
                f"cannot cancel from terminal state {self.status.value}",
            )
        # Force the transition: bypass _transition because we allow it
        # from any active state.
        self.status = TaskStatus.CANCELLED
        self.updated_at = now_iso()

    def skip(self, *, reason: str = "") -> None:
        """Any active state -> SKIPPED."""
        if self.status == TaskStatus.SKIPPED:
            return
        if TaskStatus.is_terminal(self.status):
            raise InvalidTaskError(
                self.id,
                f"cannot skip from terminal state {self.status.value}",
            )
        if reason:
            self.metadata["skip_reason"] = reason
        self.status = TaskStatus.SKIPPED
        self.updated_at = now_iso()

    def reset(self) -> None:
        """Reset the task to PENDING.

        Clears ``assigned_agent``, ``retry_count``, and the
        ``last_error`` metadata. Useful for replanning.
        """
        self.status = TaskStatus.PENDING
        self.assigned_agent = None
        self.retry_count = 0
        self.metadata.pop("last_error", None)
        self.metadata.pop("skip_reason", None)
        self.updated_at = now_iso()

    def increment_retry(self) -> int:
        """Increment ``retry_count`` and return the new value.

        Raises:
            InvalidTaskError: if max retries exhausted.
        """
        if self.retry_count >= self.max_retries:
            raise InvalidTaskError(
                self.id,
                f"max retries ({self.max_retries}) exhausted",
            )
        self.retry_count += 1
        self.updated_at = now_iso()
        return self.retry_count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """True if the task is in a terminal state."""
        return TaskStatus.is_terminal(self.status)

    @property
    def is_ready(self) -> bool:
        """True if the task is in READY state."""
        return self.status == TaskStatus.READY

    @property
    def is_running(self) -> bool:
        """True if the task is in RUNNING state."""
        return self.status == TaskStatus.RUNNING

    @property
    def is_failed(self) -> bool:
        """True if the task is in FAILED state."""
        return self.status == TaskStatus.FAILED

    @property
    def can_retry(self) -> bool:
        """True if the task can be retried (FAILED with retries left)."""
        return self.status == TaskStatus.FAILED and self.retry_count < self.max_retries

    @property
    def can_start(self) -> bool:
        """True if the task can be marked READY (PENDING state)."""
        return self.status in {TaskStatus.PENDING, TaskStatus.FAILED}

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def clone(self, *, new_id: bool = True) -> Task:
        """Return a deep copy of this task.

        Args:
            new_id: If True (default), generate a new id for the clone.
                    If False, preserve the original id (useful for
                    round-trip serialisation tests).
        """
        return Task(
            id=new_task_id("task") if new_id else self.id,
            title=self.title,
            description=self.description,
            status=self.status,
            priority=self.priority,
            dependencies=list(self.dependencies),
            required_capabilities=list(self.required_capabilities),
            assigned_agent=self.assigned_agent,
            estimated_duration=self.estimated_duration,
            estimated_tokens=self.estimated_tokens,
            estimated_cost=self.estimated_cost,
            metadata=self._deep_copy_metadata(),
            created_at=self.created_at,
            updated_at=now_iso(),
            retry_count=self.retry_count,
            max_retries=self.max_retries,
        )

    def _deep_copy_metadata(self) -> dict[str, Any]:
        """Deep-copy metadata, recursively copying nested dicts/lists."""
        import copy

        return copy.deepcopy(self.metadata)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        The output is round-trip safe: ``Task.deserialize(t.serialize())``
        produces an equivalent task (with a different ``id`` only if
        ``new_task_id`` is regenerated, which it isn't).
        """
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "dependencies": list(self.dependencies),
            "required_capabilities": list(self.required_capabilities),
            "assigned_agent": self.assigned_agent,
            "estimated_duration": self.estimated_duration,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost": self.estimated_cost,
            "metadata": self._deep_copy_metadata(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> Task:
        """Reconstruct a :class:`Task` from :meth:`serialize` output.

        Unknown keys are ignored to allow forward-compatible schema
        evolution. Missing keys fall back to dataclass defaults.
        """
        status_value = data.get("status", _DEFAULT_STATUS.value)
        priority_value = data.get("priority", _DEFAULT_PRIORITY.value)
        try:
            status = TaskStatus(status_value)
        except ValueError as exc:
            raise InvalidTaskError(
                data.get("id", "?"),
                f"unknown status '{status_value}'",
            ) from exc
        try:
            priority = TaskPriority(priority_value)
        except ValueError as exc:
            raise InvalidTaskError(
                data.get("id", "?"),
                f"unknown priority '{priority_value}'",
            ) from exc

        return cls(
            id=data.get("id") or new_task_id("task"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=status,
            priority=priority,
            dependencies=list(data.get("dependencies", [])),
            required_capabilities=list(data.get("required_capabilities", [])),
            assigned_agent=data.get("assigned_agent"),
            estimated_duration=float(data.get("estimated_duration", 0.0)),
            estimated_tokens=int(data.get("estimated_tokens", 0)),
            estimated_cost=float(data.get("estimated_cost", 0.0)),
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at") or now_iso(),
            updated_at=data.get("updated_at") or now_iso(),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def update_metadata(self, **updates: Any) -> None:
        """Merge ``updates`` into ``metadata`` and refresh ``updated_at``."""
        self.metadata = merge_dicts(self.metadata, updates)
        self.updated_at = now_iso()

    def add_dependency(self, task_id: str) -> None:
        """Add a dependency, avoiding duplicates."""
        if task_id == self.id:
            raise InvalidTaskError(self.id, "a task cannot depend on itself")
        if task_id not in self.dependencies:
            self.dependencies.append(task_id)
            self.updated_at = now_iso()

    def remove_dependency(self, task_id: str) -> None:
        """Remove a dependency if present."""
        if task_id in self.dependencies:
            self.dependencies.remove(task_id)
            self.updated_at = now_iso()

    def add_capability(self, capability: str) -> None:
        """Add a required capability, avoiding duplicates."""
        if capability and capability not in self.required_capabilities:
            self.required_capabilities.append(capability)
            self.updated_at = now_iso()

    def __repr__(self) -> str:
        return (
            f"Task(id={self.id!r}, title={self.title!r}, "
            f"status={self.status.value!r}, priority={self.priority.value!r})"
        )

    def __hash__(self) -> int:
        # Stable hash by id so Tasks can be used in sets / dict keys.
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Task):
            return self.id == other.id
        return NotImplemented


__all__ = ["Task"]
