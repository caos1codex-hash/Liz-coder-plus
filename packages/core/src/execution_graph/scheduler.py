"""Parallel Scheduler for execution graphs.

Sprint 3.8.

Analyzes the DAG, detects independent tasks, and produces execution
batches. Respects max_parallel limits and maintains synchronization
between the DAG topology and the execution state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .dependency_graph import DependencyGraph
from .ready_queue import ReadyQueue

logger = logging.getLogger(__name__)


@dataclass
class ScheduleDecision:
    """A scheduling decision for a batch of nodes.

    Attributes:
        batch:         List of node names to execute in parallel.
        level:         Topological level of the batch.
        reason:        Why these nodes were scheduled together.
        available_slots: Parallel slots remaining.
    """

    batch: list[str] = field(default_factory=list)
    level: int = 0
    reason: str = ""
    available_slots: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch": list(self.batch),
            "level": self.level,
            "reason": self.reason,
            "available_slots": self.available_slots,
        }


@dataclass
class SchedulerConfig:
    """Configuration for the ParallelScheduler.

    Attributes:
        max_parallel:    Maximum nodes executing simultaneously.
        enable_preemption: If True, higher-priority nodes can preempt.
    """

    max_parallel: int = 4
    enable_preemption: bool = False


class ParallelScheduler:
    """Schedules parallel execution of a dependency graph.

    The scheduler maintains a view of the DAG and the current
    execution state to determine which nodes are ready and how
    many can be launched concurrently.

    Args:
        graph:  The dependency graph to schedule.
        config: Scheduling configuration.
    """

    def __init__(
        self,
        graph: DependencyGraph,
        *,
        config: SchedulerConfig | None = None,
    ) -> None:
        self._graph = graph
        self._config = config or SchedulerConfig()
        self._ready_queue = ReadyQueue()
        self._running: set[str] = set()
        self._completed: set[str] = set()
        self._failed: set[str] = set()
        self._skipped: set[str] = set()
        self._cancelled: set[str] = set()
        self._retrying: set[str] = set()
        self._levels: dict[str, int] = {}
        self._scheduling_count: int = 0
        self._max_parallelism_reached: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> set[str]:
        return set(self._running)

    @property
    def completed(self) -> set[str]:
        return set(self._completed)

    @property
    def failed(self) -> set[str]:
        return set(self._failed)

    @property
    def available_slots(self) -> int:
        return max(0, self._config.max_parallel - len(self._running))

    @property
    def max_parallelism_reached(self) -> int:
        return self._max_parallelism_reached

    @property
    def scheduling_count(self) -> int:
        return self._scheduling_count

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule(self) -> ScheduleDecision:
        """Compute the next batch of nodes to execute.

        Scans for nodes whose dependencies are all completed and
        returns as many as the max_parallel limit allows.

        Returns:
            A ScheduleDecision with the batch of node names.
        """
        # Find all nodes that are ready.
        newly_ready: list[tuple[int, str]] = []  # (priority, name)

        for node in self._graph.nodes:
            if node in self._running:
                continue
            if node in self._completed or node in self._skipped or node in self._cancelled:
                continue
            if node in self._failed and node not in self._retrying:
                continue
            if self._ready_queue.contains(node):
                continue  # Already queued.

            # Check if all dependencies are completed.
            if self._is_node_ready(node):
                priority = self._graph.priorities.get(node, 0)
                newly_ready.append((priority, node))
                self._ready_queue.push(node, priority=priority)

        # Build batch from ready queue up to available slots.
        slots = self.available_slots
        batch: list[str] = []
        while slots > 0:
            name = self._ready_queue.pop()
            if name is None:
                break
            batch.append(name)
            slots -= 1

        if not batch:
            return ScheduleDecision(
                batch=[],
                level=0,
                reason="no ready nodes",
                available_slots=self.available_slots,
            )

        # Compute level for the batch.
        level = max(
            self._levels.get(n, 0) for n in batch
        ) if batch else 0

        self._scheduling_count += 1
        self._max_parallelism_reached = max(
            self._max_parallelism_reached,
            len(self._running) + len(batch),
        )

        logger.info(
            "Scheduler: batch of %d nodes at level %d: %s",
            len(batch), level, batch,
        )

        return ScheduleDecision(
            batch=batch,
            level=level,
            reason=f"scheduled {len(batch)} ready nodes",
            available_slots=slots,
        )

    def dispatch(self, node_names: list[str]) -> None:
        """Mark nodes as dispatched (running).

        Args:
            node_names: List of node names to dispatch.
        """
        for name in node_names:
            self._running.add(name)
            self._ready_queue.mark_completed(name)
        logger.debug("Scheduler: dispatched %d nodes: %s", len(node_names), node_names)

    # ------------------------------------------------------------------
    # Completion callbacks
    # ------------------------------------------------------------------

    def complete(self, node_name: str) -> list[str]:
        """Mark a node as completed and return newly ready dependents.

        Args:
            node_name: Name of the completed node.

        Returns:
            List of newly ready dependent node names.
        """
        self._running.discard(node_name)
        self._completed.add(node_name)

        # Check which dependents are now ready.
        newly_ready: list[str] = []
        for dependent in self._graph.dependents.get(node_name, set()):
            if self._is_node_ready(dependent):
                if not self._ready_queue.contains(dependent):
                    priority = self._graph.priorities.get(dependent, 0)
                    self._ready_queue.push(dependent, priority=priority)
                    newly_ready.append(dependent)

        logger.debug(
            "Scheduler: '%s' completed, %d newly ready: %s",
            node_name, len(newly_ready), newly_ready,
        )
        return newly_ready

    def fail(self, node_name: str) -> list[str]:
        """Mark a node as failed.

        Args:
            node_name: Name of the failed node.

        Returns:
            List of dependent nodes that are now blocked.
        """
        self._running.discard(node_name)
        self._failed.add(node_name)
        self._ready_queue.mark_failed(node_name)

        blocked = list(self._graph.dependents.get(node_name, set()))
        logger.debug(
            "Scheduler: '%s' failed, %d dependents blocked: %s",
            node_name, len(blocked), blocked,
        )
        return blocked

    def retry(self, node_name: str) -> None:
        """Mark a failed node for retry."""
        self._retrying.add(node_name)
        self._failed.discard(node_name)
        priority = self._graph.priorities.get(node_name, 0)
        self._ready_queue.push(node_name, priority=priority)
        logger.debug("Scheduler: '%s' queued for retry", node_name)

    def skip(self, node_name: str) -> list[str]:
        """Mark a node as skipped and propagate to dependents.

        Args:
            node_name: Name of the skipped node.

        Returns:
            List of nodes that were also skipped (transitively).
        """
        self._running.discard(node_name)
        self._failed.discard(node_name)
        self._skipped.add(node_name)
        self._ready_queue.mark_failed(node_name)

        # Propagate skip to dependents.
        skipped: list[str] = [node_name]
        stack = list(self._graph.dependents.get(node_name, set()))
        while stack:
            dep = stack.pop()
            if dep in self._skipped or dep in self._completed:
                continue
            self._skipped.add(dep)
            self._ready_queue.mark_failed(dep)
            skipped.append(dep)
            stack.extend(self._graph.dependents.get(dep, set()))

        logger.debug(
            "Scheduler: '%s' skipped, %d transitively skipped: %s",
            node_name, len(skipped), skipped,
        )
        return skipped

    def cancel(self, node_name: str) -> None:
        """Mark a node as cancelled."""
        self._running.discard(node_name)
        self._retrying.discard(node_name)
        self._cancelled.add(node_name)
        self._ready_queue.mark_failed(node_name)

    # ------------------------------------------------------------------
    # Next batch helper
    # ------------------------------------------------------------------

    def next_batch(self) -> list[str]:
        """Convenience method: schedule + dispatch in one call.

        Returns:
            List of node names dispatched, or empty list.
        """
        decision = self.schedule()
        if decision.batch:
            self.dispatch(decision.batch)
        return decision.batch

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_node_ready(self, node: str) -> bool:
        """Check if a node's dependencies are all completed."""
        deps = self._graph.edges.get(node, set())
        if not deps:
            return True
        return all(d in self._completed for d in deps)

    # ------------------------------------------------------------------
    # Level computation
    # ------------------------------------------------------------------

    def compute_levels(self) -> None:
        """Compute and cache topological levels for all nodes."""
        try:
            self._levels = self._graph.topological_levels()
        except Exception:  # noqa: BLE001
            # Fallback: BFS-based level computation.
            self._levels = {}
            for node in self._graph.nodes:
                self._levels[node] = 0
            for node in self._graph.topological_order():
                parents = self._graph.edges.get(node, set())
                if parents:
                    self._levels[node] = max(
                        self._levels.get(p, 0) for p in parents
                    ) + 1

    def get_level(self, node: str) -> int:
        """Get the topological level of a node."""
        return self._levels.get(node, 0)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    @property
    def is_finished(self) -> bool:
        """Return True if all nodes are in terminal states."""
        terminal = (
            self._completed | self._failed | self._skipped | self._cancelled
        )
        non_terminal_running = self._running - terminal
        return self._graph.nodes <= terminal and not non_terminal_running

    def pending_count(self) -> int:
        """Return the number of nodes not yet in terminal states."""
        terminal = (
            self._completed | self._failed | self._skipped | self._cancelled
        )
        return len(self._graph.nodes - terminal) - len(self._running)

    def stats(self) -> dict[str, Any]:
        """Return scheduler statistics."""
        return {
            "total_nodes": len(self._graph.nodes),
            "running": len(self._running),
            "completed": len(self._completed),
            "failed": len(self._failed),
            "skipped": len(self._skipped),
            "cancelled": len(self._cancelled),
            "retrying": len(self._retrying),
            "ready_queue_size": self._ready_queue.size,
            "available_slots": self.available_slots,
            "max_parallel": self._config.max_parallel,
            "max_parallelism_reached": self._max_parallelism_reached,
            "scheduling_count": self._scheduling_count,
            "is_finished": self.is_finished,
        }

    def reset(self) -> None:
        """Reset the scheduler state."""
        self._ready_queue.clear()
        self._running.clear()
        self._completed.clear()
        self._failed.clear()
        self._skipped.clear()
        self._cancelled.clear()
        self._retrying.clear()
        self._levels.clear()
        self._scheduling_count = 0
        self._max_parallelism_reached = 0


__all__ = ["ParallelScheduler", "ScheduleDecision", "SchedulerConfig"]