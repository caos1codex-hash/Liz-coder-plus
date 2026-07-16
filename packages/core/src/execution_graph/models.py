"""Data models for the execution graph.

Sprint 3.8.

Defines the core data structures used across the execution graph
package: DAG node representations, execution results, scheduling
decisions, and metrics.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _jsonable(value: Any) -> bool:
    try:
        import json
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


class ExecutionNodeState(str, Enum):
    """States for nodes in the execution DAG.

    Lifecycle::

        PENDING -> READY -> RUNNING -> COMPLETED
                            |-> FAILED -> RETRYING -> RUNNING
                            |-> WAITING -> READY
                            |-> SKIPPED
                            |-> CANCELLED
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[ExecutionNodeState, frozenset[ExecutionNodeState]] = {
    ExecutionNodeState.PENDING: frozenset({
        ExecutionNodeState.READY,
        ExecutionNodeState.SKIPPED,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.READY: frozenset({
        ExecutionNodeState.RUNNING,
        ExecutionNodeState.WAITING,
        ExecutionNodeState.SKIPPED,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.WAITING: frozenset({
        ExecutionNodeState.READY,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.RUNNING: frozenset({
        ExecutionNodeState.COMPLETED,
        ExecutionNodeState.FAILED,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.FAILED: frozenset({
        ExecutionNodeState.RETRYING,
        ExecutionNodeState.SKIPPED,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.RETRYING: frozenset({
        ExecutionNodeState.READY,
        ExecutionNodeState.CANCELLED,
    }),
    ExecutionNodeState.COMPLETED: frozenset(),
    ExecutionNodeState.SKIPPED: frozenset(),
    ExecutionNodeState.CANCELLED: frozenset(),
}


def can_transition_node(
    current: ExecutionNodeState, target: ExecutionNodeState
) -> bool:
    """Return True if a node can transition from *current* to *target*."""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


@dataclass
class ExecutionNode:
    """A single node in the execution DAG.

    Represents a task within a plan that can be executed in parallel
    with other independent tasks. Each node tracks its own state,
    dependencies, and execution metadata.

    Attributes:
        id:               Unique identifier (UUID4).
        name:             Short name (from Planner SubTask).
        description:      What this node should accomplish.
        agent_assigned:   Name of the agent assigned to execute.
        dependencies:     Names of sibling nodes that must complete first.
        state:            Current ExecutionNodeState.
        result:           Output of the node execution.
        error:            Last error message if the node failed.
        retry_count:      Number of retry attempts so far.
        max_retries:      Maximum allowed retries.
        started_at:       ISO timestamp when execution started.
        completed_at:     ISO timestamp when execution finished.
        created_at:       ISO timestamp when the node was created.
        execution_time_ms: Duration of the last execution attempt in ms.
        metadata:         Free-form metadata bag.
        priority:         Scheduling priority (lower = higher priority).
        topological_level: Depth level in the DAG (0 = root).
    """

    name: str
    description: str = ""
    agent_assigned: str = ""
    dependencies: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: ExecutionNodeState = ExecutionNodeState.PENDING
    result: Any = None
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str = field(default_factory=_now_iso)
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    topological_level: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            ExecutionNodeState.COMPLETED,
            ExecutionNodeState.FAILED,
            ExecutionNodeState.SKIPPED,
            ExecutionNodeState.CANCELLED,
        )

    @property
    def can_retry(self) -> bool:
        return (
            self.state == ExecutionNodeState.FAILED
            and self.retry_count < self.max_retries
        )

    def transition_to(self, new_state: ExecutionNodeState) -> None:
        """Transition to a new state with validation.

        Raises:
            ValueError: If the transition is not allowed.
        """
        if not can_transition_node(self.state, new_state):
            raise ValueError(
                f"ExecutionNode '{self.name}' cannot transition from "
                f"{self.state.value} to {new_state.value}"
            )
        self.state = new_state

        if new_state == ExecutionNodeState.RUNNING and self.started_at is None:
            self.started_at = _now_iso()
        if new_state in (
            ExecutionNodeState.COMPLETED,
            ExecutionNodeState.FAILED,
            ExecutionNodeState.SKIPPED,
            ExecutionNodeState.CANCELLED,
        ):
            self.completed_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_assigned": self.agent_assigned,
            "dependencies": list(self.dependencies),
            "state": self.state.value,
            "result": self.result if _jsonable(self.result) else str(self.result),
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "execution_time_ms": self.execution_time_ms,
            "metadata": dict(self.metadata),
            "priority": self.priority,
            "topological_level": self.topological_level,
        }


@dataclass
class ExecutionResult:
    """Result of executing an execution graph.

    Attributes:
        graph_id:          ID of the execution graph.
        status:            Final status.
        nodes_total:       Total nodes.
        nodes_completed:   Nodes completed.
        nodes_failed:      Nodes failed.
        nodes_skipped:     Nodes skipped.
        duration_ms:       Total duration in ms.
        max_parallelism:   Maximum concurrent nodes.
        retry_count:       Total retries.
        critical_path_ms:  Duration of the critical path in ms.
        error:             Fatal error if any.
    """

    graph_id: str = ""
    status: str = "pending"
    nodes_total: int = 0
    nodes_completed: int = 0
    nodes_failed: int = 0
    nodes_skipped: int = 0
    duration_ms: float = 0.0
    max_parallelism: int = 0
    retry_count: int = 0
    critical_path_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "status": self.status,
            "nodes_total": self.nodes_total,
            "nodes_completed": self.nodes_completed,
            "nodes_failed": self.nodes_failed,
            "nodes_skipped": self.nodes_skipped,
            "duration_ms": round(self.duration_ms, 2),
            "max_parallelism": self.max_parallelism,
            "retry_count": self.retry_count,
            "critical_path_ms": round(self.critical_path_ms, 2),
            "error": self.error,
        }


__all__ = [
    "ExecutionNode",
    "ExecutionNodeState",
    "ExecutionResult",
    "can_transition_node",
]