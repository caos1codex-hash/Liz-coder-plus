"""Workflow engine.

Sprint 1.7 — Phase 4.

A ``Workflow`` is a directed acyclic graph (DAG) of ``Task``s with
explicit dependencies. The ``WorkflowManager`` is responsible for:

  - Building and validating the DAG (no cycles, no missing deps).
  - Scheduling tasks whose dependencies are satisfied.
  - Running independent tasks concurrently (``asyncio.gather``).
  - Retrying failed tasks (configurable per-node).
  - Cancelling the whole workflow on critical failures.
  - Recovering from interruption (re-running PENDING/READY tasks).

Two DAG shapes are first-class:

  Sequential::

      A → B → C

  Parallel / fan-out / fan-in::

      A
       ↘
        B   →  D
       ↗
      C

The workflow does NOT execute tasks itself: it delegates to a
caller-provided async callable ``(task) -> result``. This keeps the
engine agnostic to the execution backend (Orchestrator, sub-process,
external service).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from src.memory.repositories.workflow_repository import WorkflowRepository

from src.events import (
    WORKFLOW_CANCELLED,
    WORKFLOW_COMPLETED,
    WORKFLOW_CREATED,
    WORKFLOW_FAILED,
    WORKFLOW_STARTED,
)
from src.task import Task, TaskManager, TaskState

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Workflow state machine
# ----------------------------------------------------------------------


class WorkflowState(str, Enum):
    """Lifecycle states of a Workflow."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Caller-provided executor: takes a Task, returns a result (or raises).
TaskExecutor = Callable[[Task], Awaitable[Any]]


# ----------------------------------------------------------------------
# Node definition
# ----------------------------------------------------------------------


@dataclass
class WorkflowNode:
    """A node in the workflow DAG.

    Attributes:
        task:         The underlying Task.
        depends_on:   List of task IDs that must complete before this
                      node can be scheduled.
        retries:      Maximum retry attempts on failure (default 0).
        critical:     If True, failure aborts the entire workflow
                      (default True).
    """

    task: Task
    depends_on: list[str] = field(default_factory=list)
    retries: int = 0
    critical: bool = True

    @property
    def task_id(self) -> str:
        return self.task.id


# ----------------------------------------------------------------------
# Workflow
# ----------------------------------------------------------------------


class Workflow:
    """A DAG of tasks with explicit dependencies.

    Use ``WorkflowBuilder`` to construct instances safely.
    """

    def __init__(
        self,
        *,
        name: str,
        nodes: list[WorkflowNode],
        description: str = "",
        id: str | None = None,
    ) -> None:
        self.id: str = id or str(uuid.uuid4())
        self.name = name
        self.description = description
        self._nodes: dict[str, WorkflowNode] = {n.task_id: n for n in nodes}
        self._state = WorkflowState.CREATED
        self._created_at = datetime.now(timezone.utc)
        self._started_at: datetime | None = None
        self._completed_at: datetime | None = None
        self._error: str | None = None
        # Internal event used to signal cancellation to running tasks.
        self._cancel_event = asyncio.Event()

        # Validate the DAG on construction.
        self._validate()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def is_terminal(self) -> bool:
        return self._state in (
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        )

    @property
    def nodes(self) -> list[WorkflowNode]:
        return list(self._nodes.values())

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    # ------------------------------------------------------------------
    # DAG queries
    # ------------------------------------------------------------------

    def get_node(self, task_id: str) -> WorkflowNode | None:
        return self._nodes.get(task_id)

    def roots(self) -> list[WorkflowNode]:
        """Nodes with no dependencies (entry points)."""
        return [n for n in self._nodes.values() if not n.depends_on]

    def dependents(self, task_id: str) -> list[WorkflowNode]:
        """Nodes that depend on the given task."""
        return [
            n for n in self._nodes.values() if task_id in n.depends_on
        ]

    def all_dependencies_met(self, node: WorkflowNode) -> bool:
        """Check if all dependencies of ``node`` are in COMPLETED state."""
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if dep is None:
                return False
            if dep.task.state != TaskState.COMPLETED:
                return False
        return True

    def any_dependency_failed(self, node: WorkflowNode) -> bool:
        """Check if any dependency of ``node`` is in a failure state."""
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if dep is None:
                return True
            if dep.task.state in (TaskState.FAILED, TaskState.CANCELLED):
                return True
        return False

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def mark_running(self) -> None:
        if self._state != WorkflowState.CREATED:
            raise ValueError(
                f"Workflow '{self.name}' cannot start from {self._state.value}"
            )
        self._state = WorkflowState.RUNNING
        self._started_at = datetime.now(timezone.utc)

    def mark_completed(self) -> None:
        self._state = WorkflowState.COMPLETED
        self._completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self._state = WorkflowState.FAILED
        self._error = error
        self._completed_at = datetime.now(timezone.utc)

    def mark_cancelled(self, reason: str = "") -> None:
        self._state = WorkflowState.CANCELLED
        self._error = reason or "cancelled"
        self._completed_at = datetime.now(timezone.utc)
        self._cancel_event.set()

    def request_cancel(self, reason: str = "") -> None:
        """Signal cancellation to running tasks (non-blocking)."""
        self._error = reason or "cancelled"
        self._cancel_event.set()

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        """Validate the DAG structure.

        Checks:
            - At least one node.
            - All ``depends_on`` references exist.
            - No cycles (Kahn's algorithm).
            - No self-dependencies.
        """
        if not self._nodes:
            raise ValueError("Workflow must have at least one node")

        for node in self._nodes.values():
            if node.task_id in node.depends_on:
                raise ValueError(
                    f"Node '{node.task.name}' depends on itself"
                )
            for dep_id in node.depends_on:
                if dep_id not in self._nodes:
                    raise ValueError(
                        f"Node '{node.task.name}' depends on unknown "
                        f"task '{dep_id}'"
                    )

        if self._has_cycle():
            raise ValueError(f"Workflow '{self.name}' has a cycle")

    def _has_cycle(self) -> bool:
        """Kahn's algorithm: returns True if a cycle exists."""
        in_degree = {nid: 0 for nid in self._nodes}
        for node in self._nodes.values():
            for dep_id in node.depends_on:
                # dep_id -> node.task_id (an edge from dep to node)
                in_degree[node.task_id] = in_degree.get(node.task_id, 0) + 1

        # We want to count edges correctly: in_degree[node] = number of
        # dependencies that node has.
        in_degree = {nid: 0 for nid in self._nodes}
        for node in self._nodes.values():
            in_degree[node.task_id] = len(node.depends_on)

        queue = [nid for nid, d in in_degree.items() if d == 0]
        visited = 0

        while queue:
            nid = queue.pop(0)
            visited += 1
            # Find all dependents of nid and decrement their in_degree.
            for node in self._nodes.values():
                if nid in node.depends_on:
                    in_degree[node.task_id] -= 1
                    if in_degree[node.task_id] == 0:
                        queue.append(node.task_id)

        return visited != len(self._nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "state": self._state.value,
            "node_count": self.node_count,
            "created_at": self._created_at.isoformat(),
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "completed_at": self._completed_at.isoformat() if self._completed_at else None,
            "error": self._error,
            "errors": [self._error] if self._error else [],
            "result": None,
            "metadata": {},
            "nodes": [n.task.to_dict() for n in self._nodes.values()],
            "dag_definition": {
                "nodes": [
                    {
                        "task_id": n.task_id,
                        "depends_on": list(n.depends_on),
                        "retries": n.retries,
                        "critical": n.critical,
                        "task": n.task.to_dict(),
                    }
                    for n in self._nodes.values()
                ],
            },
        }


# ----------------------------------------------------------------------
# Workflow Builder
# ----------------------------------------------------------------------


class WorkflowBuilder:
    """Fluent builder for ``Workflow`` instances.

    Example::

        wf = (
            WorkflowBuilder("ingest")
            .add_task("fetch", agent_name="fetcher")
            .add_task("parse", depends_on=["fetch"], agent_name="parser")
            .add_task("store", depends_on=["parse"], agent_name="storer")
            .build()
        )
    """

    def __init__(self, name: str, *, description: str = "") -> None:
        self._name = name
        self._description = description
        self._nodes: list[WorkflowNode] = []
        self._task_manager: TaskManager | None = None
        self._workflow_id: str | None = None

    def with_task_manager(
        self, task_manager: TaskManager, workflow_id: str | None = None
    ) -> "WorkflowBuilder":
        """Attach a TaskManager so tasks are registered on creation.

        When attached, ``add_task`` will create the underlying Task
        via the manager (emitting ``task.created`` events) and tag
        each task with the workflow id.
        """
        self._task_manager = task_manager
        self._workflow_id = workflow_id
        return self

    def add_task(
        self,
        name: str,
        *,
        description: str = "",
        depends_on: list[str] | None = None,
        agent_name: str = "",
        retries: int = 0,
        critical: bool = True,
        priority: Any = None,  # TaskPriority
        metadata: dict[str, Any] | None = None,
    ) -> "WorkflowBuilder":
        """Add a task to the workflow.

        Note: ``depends_on`` uses task NAMES within this builder, not
        ids. The builder resolves names to ids when ``build()`` is
        called.
        """
        # Stage node with name-keyed deps; resolved in build().
        node = WorkflowNode(
            task=Task(
                name=name,
                description=description,
                agent_name=agent_name,
                metadata=metadata or {},
            ),
            depends_on=list(depends_on or []),
            retries=retries,
            critical=critical,
        )
        if priority is not None:
            node.task.priority = priority
        self._nodes.append(node)
        return self

    def build(self) -> Workflow:
        """Build and validate the workflow.

        If a TaskManager is attached, tasks are registered with it
        (emitting ``task.created`` events) before building the
        Workflow. Task IDs are then re-written so ``depends_on``
        references resolve correctly.
        """
        # Resolve name-based dependencies to IDs.
        name_to_node: dict[str, WorkflowNode] = {
            n.task.name: n for n in self._nodes
        }
        for node in self._nodes:
            resolved_deps: list[str] = []
            for dep_name in node.depends_on:
                if dep_name not in name_to_node:
                    raise ValueError(
                        f"Task '{node.task.name}' depends on unknown "
                        f"task '{dep_name}'"
                    )
                resolved_deps.append(name_to_node[dep_name].task.id)
            node.depends_on = resolved_deps

        # Register tasks with the TaskManager if attached.
        if self._task_manager is not None:
            for node in self._nodes:
                # Sync registration: directly insert into the manager
                # without emitting events (callers can re-emit later).
                node.task.workflow_id = self._workflow_id
                self._task_manager._tasks[node.task.id] = node.task  # type: ignore[attr-defined]

        return Workflow(
            name=self._name,
            description=self._description,
            nodes=self._nodes,
        )

    async def build_async(self) -> Workflow:
        """Async version of ``build`` — emits task.created events.

        Tasks are created through the TaskManager (which assigns new
        IDs and emits events). Dependencies are then resolved to
        those new IDs.
        """
        # 1. Map name -> node for dependency resolution.
        name_to_node: dict[str, WorkflowNode] = {
            n.task.name: n for n in self._nodes
        }

        # 2. Create tasks through the manager (assigns new IDs).
        if self._task_manager is None:
            # No manager: fall back to sync-style resolution.
            for node in self._nodes:
                resolved_deps: list[str] = []
                for dep_name in node.depends_on:
                    if dep_name not in name_to_node:
                        raise ValueError(
                            f"Task '{node.task.name}' depends on unknown "
                            f"task '{dep_name}'"
                        )
                    resolved_deps.append(name_to_node[dep_name].task.id)
                node.depends_on = resolved_deps
            return Workflow(
                name=self._name,
                description=self._description,
                nodes=self._nodes,
            )

        # Map old_id -> new_id so we can rewrite depends_on afterwards.
        old_to_new: dict[str, str] = {}
        for node in self._nodes:
            old_id = node.task.id
            created = await self._task_manager.create(
                name=node.task.name,
                description=node.task.description,
                priority=node.task.priority,
                agent_name=node.task.agent_name,
                workflow_id=self._workflow_id,
                metadata=dict(node.task.metadata),
            )
            old_to_new[old_id] = created.id
            # Replace the node's task with the registered one.
            node.task = created

        # 3. Rewrite depends_on: name -> old_id -> new_id.
        for node in self._nodes:
            resolved_deps: list[str] = []
            for dep_name in node.depends_on:
                if dep_name not in name_to_node:
                    raise ValueError(
                        f"Task '{node.task.name}' depends on unknown "
                        f"task '{dep_name}'"
                    )
                old_dep_id = name_to_node[dep_name].task.id
                # After the rewrite above, name_to_node[dep_name].task
                # is now the registered task. So old_dep_id is actually
                # the NEW id of the dependency. We use it directly.
                resolved_deps.append(old_dep_id)
            node.depends_on = resolved_deps

        return Workflow(
            name=self._name,
            description=self._description,
            nodes=self._nodes,
        )


# ----------------------------------------------------------------------
# Workflow Manager
# ----------------------------------------------------------------------


class WorkflowManager:
    """Executes and supervises ``Workflow`` instances.

    Args:
        task_manager: Required — every workflow node is a Task in the
            shared TaskManager. This is the source of truth for state.
        event_bus:   Optional — used to emit workflow lifecycle events.
        max_concurrency: Maximum number of tasks to run in parallel.
            Defaults to 8. Use 1 for fully sequential execution.
    """

    def __init__(
        self,
        task_manager: TaskManager,
        *,
        event_bus: Any | None = None,
        max_concurrency: int = 8,
        workflow_repository: Any | None = None,
    ) -> None:
        self._tasks = task_manager
        self._bus = event_bus
        self._max_concurrency = max(1, max_concurrency)
        self._workflows: dict[str, Workflow] = {}
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._workflow_repo: Any | None = workflow_repository

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def attach_repository(self, repo: Any) -> None:
        """Attach a WorkflowRepository for persistence (late binding)."""
        self._workflow_repo = repo
        logger.info("WorkflowRepository attached")

    @property
    def count(self) -> int:
        return len(self._workflows)

    def get(self, workflow_name: str) -> Workflow | None:
        return self._workflows.get(workflow_name)

    def list_workflows(self) -> list[Workflow]:
        return list(self._workflows.values())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def register(self, workflow: Workflow) -> Workflow:
        """Register a workflow (does not start it).

        Emits ``workflow.created``.
        """
        if workflow.name in self._workflows:
            raise ValueError(
                f"Workflow '{workflow.name}' is already registered"
            )
        self._workflows[workflow.name] = workflow
        await self._emit(WORKFLOW_CREATED, {
            "workflow_name": workflow.name,
            "node_count": workflow.node_count,
        })
        await self._persist_workflow(workflow)
        return workflow

    async def run(
        self,
        workflow: Workflow,
        executor: TaskExecutor,
    ) -> Workflow:
        """Run a registered workflow to completion (or failure).

        Args:
            workflow: The workflow to run.
            executor: Async callable ``(task) -> result``.

        Returns:
            The workflow in its terminal state.
        """
        if workflow.name not in self._workflows:
            await self.register(workflow)

        workflow.mark_running()
        await self._emit(WORKFLOW_STARTED, {
            "workflow_name": workflow.name,
            "node_count": workflow.node_count,
        })
        await self._persist_workflow(workflow)

        try:
            await self._run_dag(workflow, executor)
        except asyncio.CancelledError:
            workflow.mark_cancelled("cancelled by caller")
            await self._emit(WORKFLOW_CANCELLED, {
                "workflow_name": workflow.name,
            })
            await self._persist_workflow(workflow)
            raise
        except WorkflowCancelled as exc:
            workflow.mark_cancelled(str(exc))
            await self._emit(WORKFLOW_CANCELLED, {
                "workflow_name": workflow.name,
                "reason": str(exc),
            })
        except WorkflowFailed as exc:
            workflow.mark_failed(str(exc))
            await self._emit(WORKFLOW_FAILED, {
                "workflow_name": workflow.name,
                "error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001
            workflow.mark_failed(str(exc))
            await self._emit(WORKFLOW_FAILED, {
                "workflow_name": workflow.name,
                "error": str(exc),
            })
        else:
            workflow.mark_completed()
            await self._emit(WORKFLOW_COMPLETED, {
                "workflow_name": workflow.name,
                "node_count": workflow.node_count,
            })

        await self._persist_workflow(workflow)
        return workflow

    async def cancel(self, workflow_name: str, reason: str = "") -> bool:
        """Request cancellation of a running workflow.

        Returns:
            True if the workflow was found and cancellation was
            signalled; False otherwise.
        """
        wf = self._workflows.get(workflow_name)
        if wf is None or wf.is_terminal:
            return False
        wf.request_cancel(reason)
        return True

    # ------------------------------------------------------------------
    # DAG execution
    # ------------------------------------------------------------------

    async def _run_dag(
        self, workflow: Workflow, executor: TaskExecutor
    ) -> None:
        """Execute the DAG.

        Strategy:
            - Repeat until all nodes are terminal.
            - On each iteration, find READY nodes whose dependencies
              are all COMPLETED and run them concurrently (bounded by
              the semaphore).
            - If any dependency FAILED/CANCELLED, mark dependent nodes
              as CANCELLED (unless the node is non-critical, in which
              case we skip it but continue).
            - If a critical node fails, raise WorkflowFailed.
            - If the cancel event is set, raise WorkflowCancelled.
        """
        # Use a dict keyed by task_id so we don't need WorkflowNode
        # to be hashable.
        pending: dict[str, WorkflowNode] = {
            n.task_id: n for n in workflow.nodes
        }
        attempt_counts: dict[str, int] = {tid: 0 for tid in pending}

        while pending:
            if workflow.cancel_event.is_set():
                raise WorkflowCancelled("cancel event set")

            # Find runnable nodes.
            runnable = [
                n for n in pending.values()
                if workflow.all_dependencies_met(n)
            ]

            # Handle nodes whose dependencies failed.
            blocked = [
                n for n in pending.values()
                if not workflow.all_dependencies_met(n)
                and workflow.any_dependency_failed(n)
            ]
            for n in blocked:
                if n.critical:
                    # Critical node with failed dependency: abort.
                    raise WorkflowFailed(
                        f"Critical task '{n.task.name}' blocked by "
                        f"failed dependency"
                    )
                # Non-critical: mark as CANCELLED and continue.
                if n.task.state != TaskState.CANCELLED:
                    n.task.transition_to(TaskState.CANCELLED)
                    await self._tasks.cancel(n.task_id, reason="dep failed")
                pending.pop(n.task_id, None)

            if not runnable:
                # If no runnable and no blocked but still pending,
                # we're stuck (shouldn't happen with a valid DAG).
                if pending and not blocked:
                    stuck = [n.task.name for n in pending.values()]
                    raise WorkflowFailed(
                        f"Workflow stuck; pending tasks: {stuck}"
                    )
                continue

            # Run all runnable nodes concurrently.
            coros = [
                self._run_node(workflow, n, executor, attempt_counts)
                for n in runnable
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            # Process results.
            for node, result in zip(runnable, results):
                if isinstance(result, WorkflowCancelled):
                    raise result
                if isinstance(result, WorkflowFailed):
                    if node.critical:
                        raise result
                    # Non-critical failure: mark and continue.
                    logger.warning(
                        "Non-critical task '%s' failed: %s",
                        node.task.name, result,
                    )
                if isinstance(result, Exception):
                    if node.critical:
                        raise WorkflowFailed(
                            f"Task '{node.task.name}' raised: {result}"
                        ) from result
                # Successful nodes are removed from pending.
                pending.pop(node.task_id, None)

    async def _run_node(
        self,
        workflow: Workflow,
        node: WorkflowNode,
        executor: TaskExecutor,
        attempt_counts: dict[str, int],
    ) -> None:
        """Execute a single node with retries and concurrency limit."""
        async with self._semaphore:
            if workflow.cancel_event.is_set():
                raise WorkflowCancelled("cancel event set")

            task = node.task
            attempt = attempt_counts[task.id]

            while True:
                attempt += 1
                attempt_counts[task.id] = attempt

                # Transition through READY → RUNNING.
                if task.state == TaskState.PENDING:
                    task.transition_to(TaskState.READY)
                if task.state == TaskState.FAILED:
                    # Retry: FAILED → READY.
                    task.transition_to(TaskState.READY)

                await self._tasks.start(task.id)

                try:
                    result = await executor(task)
                except asyncio.CancelledError:
                    await self._tasks.cancel(task.id, reason="cancelled")
                    raise WorkflowCancelled(
                        f"Task '{task.name}' cancelled"
                    )
                except Exception as exc:  # noqa: BLE001
                    if attempt <= node.retries:
                        logger.info(
                            "Task '%s' failed (attempt %d/%d): %s — retrying",
                            task.name, attempt, node.retries + 1, exc,
                        )
                        await self._tasks.fail(task.id, str(exc))
                        # Loop back to retry.
                        continue
                    await self._tasks.fail(task.id, str(exc))
                    raise WorkflowFailed(
                        f"Task '{task.name}' failed after "
                        f"{attempt} attempt(s): {exc}"
                    ) from exc

                await self._tasks.complete(
                    task.id, result=result, tools_used=[]
                )
                return

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def load_from_persistence(self) -> int:
        """Load active workflows from the database into the in-memory dict.

        Returns the number of workflows loaded. No-op if no repository
        is attached.
        """
        if self._workflow_repo is None:
            return 0
        try:
            rows = await self._workflow_repo.get_active_workflows()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load workflows from persistence")
            return 0
        loaded = 0
        for row in rows:
            try:
                wf = self._dict_to_workflow(row)
                self._workflows[wf.name] = wf
                loaded += 1
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to restore workflow from persistence: %s",
                    row.get("id"),
                    exc_info=True,
                )
        if loaded:
            logger.info("Loaded %d active workflows from persistence", loaded)
        return loaded

    async def _emit(self, event_type: str, payload: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)

    async def _persist_workflow(self, workflow: Workflow) -> None:
        """Persist a workflow to the database if a repo is attached.

        Errors are logged but never raised (graceful degradation).
        """
        if self._workflow_repo is None:
            return
        try:
            await self._workflow_repo.save_workflow(workflow.to_dict())  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to persist workflow '%s' (%s)",
                workflow.name, workflow.id,
            )

    @staticmethod
    def _dict_to_workflow(data: dict[str, Any]) -> Workflow:
        """Reconstruct a Workflow from a persistence dict."""
        from src.task import Task, TaskState, TaskPriority

        def _parse_dt(value: Any) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(value)

        dag = data.get("dag_definition", {})
        node_defs = dag.get("nodes", []) if isinstance(dag, dict) else []

        nodes: list[WorkflowNode] = []
        for nd in node_defs:
            task_data = nd.get("task", {})
            task = Task(
                name=task_data.get("name", "unknown"),
                description=task_data.get("description", ""),
                priority=TaskPriority(task_data.get("priority", "normal")),
                id=task_data.get("id", str(uuid.uuid4())),
                state=TaskState(task_data.get("state", "pending")),
                progress=int(task_data.get("progress", 0)),
                agent_name=task_data.get("agent_name", ""),
                tools_used=list(task_data.get("tools_used", [])),
                created_at=_parse_dt(task_data.get("created_at")) or datetime.now(timezone.utc),
                started_at=_parse_dt(task_data.get("started_at")),
                completed_at=_parse_dt(task_data.get("completed_at")),
                result=task_data.get("result"),
                errors=list(task_data.get("errors", [])),
                metadata=dict(task_data.get("metadata", {})),
                parent_id=task_data.get("parent_id"),
                workflow_id=task_data.get("workflow_id") or data.get("id"),
            )
            node = WorkflowNode(
                task=task,
                depends_on=list(nd.get("depends_on", [])),
                retries=int(nd.get("retries", 0)),
                critical=bool(nd.get("critical", True)),
            )
            nodes.append(node)

        # Reconstruct with existing timestamps.
        wf = Workflow(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            nodes=nodes,
            id=data.get("id"),
        )
        # Restore timestamps and state.
        created = _parse_dt(data.get("created_at"))
        if created is not None:
            wf._created_at = created
        started = _parse_dt(data.get("started_at"))
        if started is not None:
            wf._started_at = started
        completed = _parse_dt(data.get("completed_at"))
        if completed is not None:
            wf._completed_at = completed
        try:
            wf._state = WorkflowState(data.get("state", "created"))
        except ValueError:
            wf._state = WorkflowState.CREATED
        errors = data.get("errors", [])
        if isinstance(errors, list) and errors:
            wf._error = errors[0] if errors else None
        return wf


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------


class WorkflowFailed(Exception):
    """Raised when a critical task fails."""


class WorkflowCancelled(Exception):
    """Raised when a workflow is cancelled."""


__all__ = [
    "TaskExecutor",
    "Workflow",
    "WorkflowBuilder",
    "WorkflowCancelled",
    "WorkflowFailed",
    "WorkflowManager",
    "WorkflowNode",
    "WorkflowState",
]
