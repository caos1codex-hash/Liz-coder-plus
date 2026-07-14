"""ExecutionPlan — container for a planned set of tasks (Sprint 2.8).

An :class:`ExecutionPlan` bundles:

- A collection of :class:`~planner.task.Task` objects (keyed by id).
- A :class:`~planner.graph.DAG` capturing dependencies between them.
- Pre-computed ``parallel_groups`` (layers) and ``execution_order``
  (topological sort), refreshed whenever the plan changes.
- Free-form ``metadata`` (plan id, objective, source, etc.).
- ``statistics`` aggregating per-status counts and total estimates.

The plan is the unit of work handed to the
:class:`~planner.scheduler.Scheduler`. It is serializable so it can be
persisted to memory, sent over a wire, or compared across revisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .exceptions import (
    PlanValidationError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from .graph import DAG
from .models import PlanStatistics, TaskStatus
from .task import Task
from .utils import merge_dicts, new_task_id, now_iso

# ----------------------------------------------------------------------
# ExecutionPlan
# ----------------------------------------------------------------------


@dataclass
class ExecutionPlan:
    """An ordered set of tasks with a dependency graph.

    Attributes:
        id:           Unique plan id (auto-generated if absent).
        objective:    High-level goal the plan was created to achieve.
        tasks:        Dict ``{task_id: Task}`` (do not mutate directly;
                      use :meth:`add_task` / :meth:`remove_task`).
        graph:        DAG of task dependencies.
        metadata:     Free-form metadata (source, version, tags, ...).
        created_at:   ISO timestamp of plan creation.
        updated_at:   ISO timestamp of last mutation.
    """

    id: str = field(default_factory=lambda: new_task_id("plan"))
    objective: str = ""
    tasks: dict[str, Task] = field(default_factory=dict)
    graph: DAG = field(default_factory=DAG)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # Cached views (rebuilt on every mutation).
    _parallel_groups: list[list[str]] = field(default_factory=list, repr=False)
    _execution_order: list[str] = field(default_factory=list, repr=False)
    _statistics: PlanStatistics = field(default_factory=PlanStatistics, repr=False)
    _dirty: bool = True

    # ------------------------------------------------------------------
    # __post_init__
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        # Defensive copies.
        self.tasks = dict(self.tasks)
        self.metadata = dict(self.metadata)
        if not isinstance(self.graph, DAG):
            raise TypeError("graph must be a DAG instance")
        # If tasks were supplied but graph is empty, rebuild the graph
        # from task.dependencies. This makes ExecutionPlan resilient
        # to deserialized inputs that only carried task-level deps.
        if self.tasks and len(self.graph) == 0:
            self._rebuild_graph_from_tasks()
        self._rebuild_caches()

    def _rebuild_graph_from_tasks(self) -> None:
        """Populate ``graph`` from ``task.dependencies``."""
        for task in self.tasks.values():
            self.graph.add_task(task.id)
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep in self.tasks:
                    try:
                        self.graph.add_dependency(task.id, dep)
                    except Exception:  # noqa: BLE001
                        # Ignore broken edges; validate() will report them.
                        pass

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_task(self, task: Task) -> Task:
        """Add ``task`` to the plan and update the graph.

        Returns the added task (for chaining).

        Raises:
            TaskAlreadyExistsError: if a task with the same id exists.
        """
        if task.id in self.tasks:
            raise TaskAlreadyExistsError(task.id)
        self.tasks[task.id] = task
        self.graph.add_task(task.id)
        for dep in task.dependencies:
            if dep in self.tasks:
                # Only add edges for known deps; otherwise validate()
                # will flag the broken dependency.
                try:
                    self.graph.add_dependency(task.id, dep)
                except Exception:  # noqa: BLE001
                    pass
        self._mark_dirty()
        return task

    def remove_task(self, task_id: str) -> bool:
        """Remove ``task_id`` from the plan and clean up the graph.

        Returns True if the task was present and removed.
        Also removes the task from any other task's ``dependencies``
        list (but does not silently rewrite deps — instead, those tasks
        are left with a broken dep that :meth:`validate` will report).
        """
        if task_id not in self.tasks:
            return False
        del self.tasks[task_id]
        self.graph.remove_task(task_id)
        # Clean up references in other tasks' dependencies lists.
        for t in self.tasks.values():
            if task_id in t.dependencies:
                t.dependencies = [d for d in t.dependencies if d != task_id]
        self._mark_dirty()
        return True

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Declare that ``task_id`` depends on ``depends_on``.

        Updates both the graph and the task's ``dependencies`` list.

        Raises:
            TaskNotFoundError: if either id is not in the plan.
        """
        self._require_task(task_id)
        self._require_task(depends_on)
        self.graph.add_dependency(task_id, depends_on)
        self.tasks[task_id].add_dependency(depends_on)
        self._mark_dirty()

    def remove_dependency(self, task_id: str, depends_on: str) -> None:
        """Remove a dependency edge.

        Idempotent.
        """
        if task_id not in self.tasks:
            return
        self.graph.remove_dependency(task_id, depends_on)
        self.tasks[task_id].remove_dependency(depends_on)
        self._mark_dirty()

    def clear(self) -> None:
        """Remove all tasks and reset the graph."""
        self.tasks.clear()
        self.graph = DAG()
        self._mark_dirty()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> Task | None:
        """Return the task with ``task_id`` or None."""
        return self.tasks.get(task_id)

    def require_task(self, task_id: str) -> Task:
        """Return the task or raise :class:`TaskNotFoundError`."""
        t = self.tasks.get(task_id)
        if t is None:
            raise TaskNotFoundError(task_id)
        return t

    def _require_task(self, task_id: str) -> None:
        """Raise :class:`TaskNotFoundError` if ``task_id`` is missing."""
        if task_id not in self.tasks:
            raise TaskNotFoundError(task_id)

    def __contains__(self, task_id: object) -> bool:
        return isinstance(task_id, str) and task_id in self.tasks

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks.values())

    @property
    def task_ids(self) -> list[str]:
        """All task ids, sorted."""
        return sorted(self.tasks.keys())

    @property
    def parallel_groups(self) -> list[list[str]]:
        """Layers of tasks that can be executed concurrently (cached)."""
        self._rebuild_if_dirty()
        return [list(g) for g in self._parallel_groups]

    @property
    def execution_order(self) -> list[str]:
        """Topological execution order (cached)."""
        self._rebuild_if_dirty()
        return list(self._execution_order)

    @property
    def statistics(self) -> PlanStatistics:
        """Aggregate statistics (cached)."""
        self._rebuild_if_dirty()
        return self._statistics

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors.

        Empty list means the plan is structurally valid. Checks:

        - Graph is acyclic.
        - All task.dependencies reference tasks that exist.
        - All required_capabilities are non-empty strings.
        """
        errors: list[str] = list(self.graph.validate())
        # Broken dependencies (task lists a dep not in the plan).
        for task in self.tasks.values():
            for dep in task.dependencies:
                if dep not in self.tasks:
                    errors.append(f"Task '{task.id}' depends on unknown task '{dep}'")
            for cap in task.required_capabilities:
                if not isinstance(cap, str) or not cap:
                    errors.append(f"Task '{task.id}' has an invalid required_capability: {cap!r}")
        return errors

    def assert_valid(self) -> None:
        """Raise :class:`PlanValidationError` if :meth:`validate` finds issues."""
        errors = self.validate()
        if errors:
            raise PlanValidationError(errors, plan_id=self.id)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a JSON-compatible summary of the plan.

        Includes id, objective, task count, statistics, parallel groups
        and critical path.
        """
        self._rebuild_if_dirty()
        # Build critical path from estimated durations.
        durations = {tid: self.tasks[tid].estimated_duration for tid in self.tasks}
        try:
            critical = self.graph.critical_path(durations)
        except Exception:  # noqa: BLE001
            critical = []
        return {
            "id": self.id,
            "objective": self.objective,
            "task_count": len(self.tasks),
            "statistics": self._statistics.to_dict(),
            "parallel_groups": [list(g) for g in self._parallel_groups],
            "execution_order": list(self._execution_order),
            "critical_path": critical,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize the plan to a JSON-compatible dict.

        Round-trip safe: ``ExecutionPlan.deserialize(p.serialize())``
        produces an equivalent plan.
        """
        self._rebuild_if_dirty()
        return {
            "id": self.id,
            "objective": self.objective,
            "tasks": [t.serialize() for t in self.tasks.values()],
            "graph": self.graph.to_dict(),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> ExecutionPlan:
        """Reconstruct an :class:`ExecutionPlan` from :meth:`serialize` output."""
        tasks: dict[str, Task] = {}
        for task_data in data.get("tasks", []):
            t = Task.deserialize(task_data)
            tasks[t.id] = t
        graph_data = data.get("graph")
        graph = DAG.from_dict(graph_data) if graph_data else DAG()
        return cls(
            id=data.get("id") or new_task_id("plan"),
            objective=data.get("objective", ""),
            tasks=tasks,
            graph=graph,
            metadata=dict(data.get("metadata", {})),
            created_at=data.get("created_at") or now_iso(),
            updated_at=data.get("updated_at") or now_iso(),
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def update_metadata(self, **updates: Any) -> None:
        """Merge ``updates`` into ``metadata`` and refresh ``updated_at``."""
        self.metadata = merge_dicts(self.metadata, updates)
        self.updated_at = now_iso()

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Force-update a task's status (used by the scheduler).

        Bypasses transition validation; use :meth:`Task.mark_*` for
        validated transitions.
        """
        t = self.require_task(task_id)
        t.status = status
        t.updated_at = now_iso()
        self._mark_dirty()

    def clone(self, *, new_id: bool = True) -> ExecutionPlan:
        """Return a deep copy of this plan.

        Args:
            new_id: If True (default), generate a new plan id.
        """
        return ExecutionPlan(
            id=new_task_id("plan") if new_id else self.id,
            objective=self.objective,
            tasks={tid: t.clone(new_id=False) for tid, t in self.tasks.items()},
            graph=self.graph.copy(),
            metadata=dict(self.metadata),
            created_at=self.created_at,
            updated_at=now_iso(),
        )

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.updated_at = now_iso()

    def _rebuild_if_dirty(self) -> None:
        if self._dirty:
            self._rebuild_caches()
            self._dirty = False

    def _rebuild_caches(self) -> None:
        # Execution order (topological sort).
        try:
            self._execution_order = self.graph.topological_sort()
            self._parallel_groups = self.graph.parallel_layers()
        except Exception:  # noqa: BLE001
            # If the graph has cycles, leave the caches empty; validate()
            # will report the error.
            self._execution_order = []
            self._parallel_groups = []
        # Statistics.
        self._statistics = self._compute_statistics()

    def _compute_statistics(self) -> PlanStatistics:
        stats = PlanStatistics()
        stats.total_tasks = len(self.tasks)
        for t in self.tasks.values():
            match t.status:
                case TaskStatus.PENDING:
                    stats.pending += 1
                case TaskStatus.READY:
                    stats.ready += 1
                case TaskStatus.RUNNING:
                    stats.running += 1
                case TaskStatus.WAITING:
                    stats.waiting += 1
                case TaskStatus.COMPLETED:
                    stats.completed += 1
                case TaskStatus.FAILED:
                    stats.failed += 1
                case TaskStatus.CANCELLED:
                    stats.cancelled += 1
                case TaskStatus.SKIPPED:
                    stats.skipped += 1
            stats.total_estimated_duration += t.estimated_duration
            stats.total_estimated_tokens += t.estimated_tokens
            stats.total_estimated_cost += t.estimated_cost
        stats.parallel_groups = len(self._parallel_groups)
        # Critical path length.
        durations = {tid: self.tasks[tid].estimated_duration for tid in self.tasks}
        try:
            stats.critical_path_len = len(self.graph.critical_path(durations))
        except Exception:  # noqa: BLE001
            stats.critical_path_len = 0
        return stats

    def __repr__(self) -> str:
        return (
            f"ExecutionPlan(id={self.id!r}, "
            f"objective={self.objective!r}, "
            f"tasks={len(self.tasks)})"
        )


__all__ = ["ExecutionPlan"]
