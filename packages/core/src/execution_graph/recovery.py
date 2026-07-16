"""Recovery Manager for execution graphs.

Sprint 3.8.

Handles failure recovery, partial replanning, rollback, and
resume operations for execution graphs that encounter errors.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from .dependency_graph import DependencyGraph
from .models import ExecutionNode, ExecutionNodeState

if TYPE_CHECKING:
    from .parallel_executor import ParallelExecutor

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoveryCheckpoint:
    """A snapshot of execution state for recovery.

    Attributes:
        id:                Unique identifier.
        graph_id:          ID of the execution graph.
        timestamp:         ISO timestamp.
        completed_nodes:   Set of completed node names.
        failed_nodes:      Set of failed node names.
        skipped_nodes:     Set of skipped node names.
        node_results:      Dict {name: result}.
        node_errors:       Dict {name: error message}.
        node_retries:      Dict {name: retry_count}.
        scheduler_stats:   Scheduler statistics snapshot.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    graph_id: str = ""
    timestamp: str = field(default_factory=_now_iso)
    completed_nodes: set[str] = field(default_factory=set)
    failed_nodes: set[str] = field(default_factory=set)
    skipped_nodes: set[str] = field(default_factory=set)
    node_results: dict[str, Any] = field(default_factory=dict)
    node_errors: dict[str, str] = field(default_factory=dict)
    node_retries: dict[str, int] = field(default_factory=dict)
    scheduler_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "graph_id": self.graph_id,
            "timestamp": self.timestamp,
            "completed_nodes": sorted(self.completed_nodes),
            "failed_nodes": sorted(self.failed_nodes),
            "skipped_nodes": sorted(self.skipped_nodes),
            "node_results": {
                k: v for k, v in self.node_results.items()
            },
            "node_errors": dict(self.node_errors),
            "node_retries": dict(self.node_retries),
            "scheduler_stats": dict(self.scheduler_stats),
        }


class RecoveryManager:
    """Manages recovery operations for execution graphs.

    Provides checkpointing, rollback, and partial replanning
    capabilities. When a node fails, the RecoveryManager can:

    - Retry the failed node.
    - Skip the failed node and its dependents.
    - Replan the affected subgraph.
    - Rollback to a previous checkpoint.
    - Resume from the point of failure.

    Args:
        graph:  The dependency graph.
        nodes:  Dict of ExecutionNode instances.
    """

    def __init__(
        self,
        graph: DependencyGraph,
        nodes: dict[str, ExecutionNode],
    ) -> None:
        self._graph = graph
        self._nodes = nodes
        self._checkpoints: list[RecoveryCheckpoint] = []
        self._max_checkpoints = 10

    # ------------------------------------------------------------------
    # Checkpointing
    # ------------------------------------------------------------------

    def save_checkpoint(self, graph_id: str = "", stats: dict[str, Any] | None = None) -> RecoveryCheckpoint:
        """Save a checkpoint of the current execution state.

        Args:
            graph_id: ID of the execution graph.
            stats:    Optional scheduler stats to include.

        Returns:
            The created RecoveryCheckpoint.
        """
        completed = {
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.COMPLETED
        }
        failed = {
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.FAILED
        }
        skipped = {
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.SKIPPED
        }

        checkpoint = RecoveryCheckpoint(
            graph_id=graph_id,
            completed_nodes=completed,
            failed_nodes=failed,
            skipped_nodes=skipped,
            node_results={
                n: nd.result for n, nd in self._nodes.items()
                if n in completed and nd.result is not None
            },
            node_errors={
                n: nd.error for n in failed
                if self._nodes[n].error is not None
            },
            node_retries={
                n: nd.retry_count for n, nd in self._nodes.items()
                if nd.retry_count > 0
            },
            scheduler_stats=stats or {},
        )

        self._checkpoints.append(checkpoint)
        if len(self._checkpoints) > self._max_checkpoints:
            self._checkpoints.pop(0)

        logger.info(
            "RecoveryManager: checkpoint saved (id=%s, "
            "completed=%d, failed=%d)",
            checkpoint.id[:8], len(completed), len(failed),
        )
        return checkpoint

    def get_latest_checkpoint(self) -> RecoveryCheckpoint | None:
        """Return the most recent checkpoint, or None."""
        return self._checkpoints[-1] if self._checkpoints else None

    # ------------------------------------------------------------------
    # Recovery operations
    # ------------------------------------------------------------------

    def recover_task(
        self,
        node_name: str,
        *,
        strategy: str = "retry",
    ) -> dict[str, Any]:
        """Recover a single failed task.

        Args:
            node_name: Name of the failed node.
            strategy:  Recovery strategy: 'retry', 'skip', or 'replan'.

        Returns:
            Dict with recovery details.
        """
        node = self._nodes.get(node_name)
        if node is None:
            return {"error": f"Node '{node_name}' not found"}

        if node.state != ExecutionNodeState.FAILED:
            return {
                "error": f"Node '{node_name}' is in state {node.state.value}, "
                f"not FAILED",
            }

        if strategy == "retry":
            return self._retry_node(node)
        elif strategy == "skip":
            return self._skip_node(node)
        elif strategy == "replan":
            return self._replan_node(node)
        else:
            return {"error": f"Unknown recovery strategy: {strategy}"}

    def recover_plan(self) -> dict[str, Any]:
        """Attempt to recover the entire plan by retrying all failed nodes.

        Returns:
            Dict with recovery details.
        """
        failed_nodes = [
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.FAILED
        ]

        if not failed_nodes:
            return {"recovered": False, "reason": "no failed nodes"}

        # Reset all failed nodes to RETRYING.
        recovered: list[str] = []
        for name in failed_nodes:
            node = self._nodes[name]
            result = self._retry_node(node)
            if "error" not in result:
                recovered.append(name)

        return {
            "recovered": True,
            "recovered_nodes": recovered,
            "total_failed": len(failed_nodes),
        }

    def rollback(self, checkpoint_id: str | None = None) -> dict[str, Any]:
        """Rollback to a previous checkpoint.

        Args:
            checkpoint_id: Specific checkpoint to rollback to.
                           If None, uses the latest.

        Returns:
            Dict with rollback details.
        """
        checkpoint = self._get_checkpoint(checkpoint_id)
        if checkpoint is None:
            return {"error": "No checkpoint found"}

        restored = 0
        reset_to_pending: list[str] = []

        for name, node in self._nodes.items():
            if name in checkpoint.completed_nodes:
                # Keep completed nodes as-is.
                restored += 1
            elif name in checkpoint.failed_nodes:
                # Reset failed nodes to PENDING for re-execution.
                node.state = ExecutionNodeState.PENDING
                node.error = None
                node.retry_count = checkpoint.node_retries.get(name, 0)
                node.started_at = None
                node.completed_at = None
                restored += 1
            elif name in checkpoint.skipped_nodes:
                # Reset skipped nodes to PENDING.
                node.state = ExecutionNodeState.PENDING
                node.error = None
                node.started_at = None
                node.completed_at = None
                restored += 1
            else:
                # Nodes that were RUNNING or READY at checkpoint time.
                if not node.is_terminal:
                    node.state = ExecutionNodeState.PENDING
                    node.started_at = None
                    node.completed_at = None
                    reset_to_pending.append(name)

        logger.info(
            "RecoveryManager: rolled back to checkpoint %s "
            "(restored=%d, reset=%d)",
            checkpoint.id[:8], restored, len(reset_to_pending),
        )

        return {
            "checkpoint_id": checkpoint.id,
            "restored": restored,
            "reset_to_pending": reset_to_pending,
        }

    def resume(self) -> dict[str, Any]:
        """Resume execution from the current state.

        Identifies completed nodes and returns the set of nodes
        that still need execution.

        Returns:
            Dict with resume information.
        """
        completed = {
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.COMPLETED
        }
        pending = {
            n for n, node in self._nodes.items()
            if not node.is_terminal
        }

        # Nodes that are ready to execute (all deps completed).
        ready = []
        for name in pending:
            deps = self._graph.edges.get(name, set())
            if all(d in completed for d in deps):
                ready.append(name)

        logger.info(
            "RecoveryManager: resume (completed=%d, pending=%d, ready=%d)",
            len(completed), len(pending), len(ready),
        )

        return {
            "completed": sorted(completed),
            "pending": sorted(pending),
            "ready": sorted(ready),
        }

    def continue_from_failure(
        self,
        failed_node: str,
        *,
        skip_failed: bool = True,
    ) -> dict[str, Any]:
        """Continue execution after a node failure.

        Args:
            failed_node:  Name of the failed node.
            skip_failed:  If True, skip the failed node and continue
                          with its dependents' siblings.

        Returns:
            Dict with recovery details.
        """
        result: dict[str, Any] = {
            "failed_node": failed_node,
            "skip_failed": skip_failed,
        }

        if skip_failed:
            # Get affected subgraph.
            affected = self._graph.affected_subgraph(failed_node)
            skipped_names: list[str] = []

            for name in affected.nodes:
                node = self._nodes.get(name)
                if node and (not node.is_terminal or node.state == ExecutionNodeState.FAILED):
                    node.state = ExecutionNodeState.SKIPPED
                    node.completed_at = _now_iso()
                    skipped_names.append(name)

            result["skipped_nodes"] = skipped_names
            result["affected_count"] = len(skipped_names)

        # Compute what can still run.
        completed = {
            n for n, node in self._nodes.items()
            if node.state == ExecutionNodeState.COMPLETED
        }
        runnable: list[str] = []
        for name, node in self._nodes.items():
            if node.is_terminal:
                continue
            deps = self._graph.edges.get(name, set())
            if all(d in completed for d in deps):
                runnable.append(name)

        result["runnable_nodes"] = runnable
        return result

    # ------------------------------------------------------------------
    # Partial replanning
    # ------------------------------------------------------------------

    def partial_replan(
        self,
        failed_node: str,
        *,
        replan_fn: Any | None = None,
    ) -> dict[str, Any]:
        """Replan only the affected subgraph after a failure.

    When a task fails, this does NOT regenerate the entire plan.
    Instead, it identifies the affected subgraph (the failed node
    plus all its transitive dependents) and returns it for
    selective replanning.

    Args:
        failed_node: Name of the failed node.
        replan_fn:   Optional async function to generate replacement
                     nodes. Signature: (failed_node, affected_nodes) -> list[dict].

    Returns:
        Dict with the affected subgraph and optional new plan.
    """
        affected = self._graph.affected_subgraph(failed_node)

        result: dict[str, Any] = {
            "failed_node": failed_node,
            "affected_nodes": sorted(affected.nodes),
            "affected_edges": affected.to_dict()["edges"],
        }

        if replan_fn is not None:
            try:
                # replan_fn can be sync or async.
                import asyncio
                if asyncio.iscoroutinefunction(replan_fn):
                    new_plan = asyncio.get_event_loop().run_until_complete(
                        replan_fn(failed_node, sorted(affected.nodes))
                    )
                else:
                    new_plan = replan_fn(failed_node, sorted(affected.nodes))
                result["new_plan"] = new_plan
            except Exception as exc:  # noqa: BLE001
                result["replan_error"] = str(exc)

        logger.info(
            "RecoveryManager: partial replan for '%s' "
            "(affected=%d nodes)",
            failed_node, len(affected.nodes),
        )
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _retry_node(self, node: ExecutionNode) -> dict[str, Any]:
        """Reset a failed node for retry."""
        node.state = ExecutionNodeState.PENDING
        node.error = None
        node.started_at = None
        return {
            "action": "retry",
            "node": node.name,
            "new_state": "pending",
        }

    def _skip_node(self, node: ExecutionNode) -> dict[str, Any]:
        """Skip a failed node and propagate to dependents."""
        node.state = ExecutionNodeState.SKIPPED
        node.completed_at = _now_iso()
        skipped = [node.name]

        # Propagate.
        stack = list(self._graph.dependents.get(node.name, set()))
        while stack:
            dep_name = stack.pop()
            dep = self._nodes.get(dep_name)
            if dep and not dep.is_terminal:
                dep.state = ExecutionNodeState.SKIPPED
                dep.completed_at = _now_iso()
                skipped.append(dep_name)
                stack.extend(self._graph.dependents.get(dep_name, set()))

        return {
            "action": "skip",
            "node": node.name,
            "transitively_skipped": skipped[1:],
        }

    def _replan_node(self, node: ExecutionNode) -> dict[str, Any]:
        """Mark a node for replanning."""
        affected = self._graph.affected_subgraph(node.name)
        return {
            "action": "replan",
            "node": node.name,
            "affected_nodes": sorted(affected.nodes),
        }

    def _get_checkpoint(
        self, checkpoint_id: str | None
    ) -> RecoveryCheckpoint | None:
        if checkpoint_id is None:
            return self.get_latest_checkpoint()
        for cp in self._checkpoints:
            if cp.id == checkpoint_id:
                return cp
        return None

    def checkpoints(self) -> list[dict[str, Any]]:
        """Return all checkpoints as dicts."""
        return [cp.to_dict() for cp in self._checkpoints]

    def clear_checkpoints(self) -> None:
        """Remove all saved checkpoints."""
        self._checkpoints.clear()


__all__ = ["RecoveryCheckpoint", "RecoveryManager"]