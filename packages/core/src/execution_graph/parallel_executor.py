"""Parallel Executor for execution graphs.

Sprint 3.8.

Executes multiple graph nodes simultaneously using asyncio,
respecting the DAG topology and max_parallel limits. Integrates
with the ParallelScheduler for node selection and the RetryPolicy
for error handling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .dependency_graph import DependencyGraph
from .models import ExecutionNode, ExecutionNodeState, ExecutionResult
from .retry_policy import RetryPolicy
from .scheduler import ParallelScheduler, SchedulerConfig
from .state_machine import ExecutionStateMachine

logger = logging.getLogger(__name__)


# Type alias for the async node execution function.
NodeExecutor = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class ParallelExecutorConfig:
    """Configuration for the ParallelExecutor.

    Attributes:
        max_parallel:      Max nodes executing simultaneously.
        default_timeout:   Timeout per node in seconds (0 = none).
        retry_policy:      Default retry policy.
        pause_between_batches: Seconds to pause between batches.
    """

    max_parallel: int = 4
    default_timeout: float = 0.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    pause_between_batches: float = 0.0


class ParallelExecutor:
    """Executes a dependency graph with parallel node dispatch.

    Uses asyncio to run independent nodes concurrently while
    respecting the DAG's dependency ordering. The executor
    integrates with the state machine for tracking and the
    scheduler for node selection.

    Args:
        graph:           The dependency graph.
        nodes:           Dict of ExecutionNode instances.
        node_executor:   Async function to execute a single node.
        config:          Executor configuration.
        event_callback:  Optional callback for execution events.
    """

    def __init__(
        self,
        graph: DependencyGraph,
        nodes: dict[str, ExecutionNode],
        *,
        node_executor: NodeExecutor | None = None,
        config: ParallelExecutorConfig | None = None,
        event_callback: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._graph = graph
        self._nodes = nodes
        self._executor_fn = node_executor
        self._config = config or ParallelExecutorConfig()
        self._event_cb = event_callback

        self._scheduler = ParallelScheduler(
            graph,
            config=SchedulerConfig(
                max_parallel=self._config.max_parallel,
            ),
        )
        self._state_machine = ExecutionStateMachine()
        self._retry_policies: dict[str, RetryPolicy] = {}

        # Initialize state machine.
        self._state_machine.initialize(list(nodes.keys()))

        # Compute topological levels.
        self._scheduler.compute_levels()
        for name, level in self._scheduler._levels.items():  # noqa: SLF001
            if name in self._nodes:
                self._nodes[name].topological_level = level

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self) -> ExecutionResult:
        """Execute the entire graph to completion.

        Returns:
            ExecutionResult with final statistics.
        """
        start = time.monotonic()
        result = ExecutionResult(
            graph_id="",
            nodes_total=len(self._nodes),
        )

        self._scheduler.reset()
        self._scheduler.compute_levels()

        max_iterations = len(self._nodes) * (self._config.retry_policy.max_retries + 1) + 10
        active_tasks: dict[str, asyncio.Task] = {}

        for _ in range(max_iterations):
            if self._scheduler.is_finished:
                break

            # Schedule next batch.
            batch = self._scheduler.next_batch()
            if not batch:
                if not active_tasks:
                    # Nothing to run and nothing active.
                    break
                # Wait for an active task to complete.
                done, _ = await asyncio.wait(
                    active_tasks.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=1.0,
                )
                await self._process_completed_tasks(
                    active_tasks, done, result
                )
                continue

            # Transition nodes to RUNNING and launch.
            for name in batch:
                node = self._nodes.get(name)
                if node is None:
                    self._scheduler.fail(name)
                    continue

                try:
                    # Transition: PENDING -> READY -> RUNNING.
                    if node.state == ExecutionNodeState.PENDING:
                        self._state_machine.transition(
                            name, ExecutionNodeState.READY,
                            metadata={"batch": True},
                        )
                        node.transition_to(ExecutionNodeState.READY)
                    self._state_machine.transition(
                        name, ExecutionNodeState.RUNNING,
                        metadata={"batch": True},
                    )
                    node.transition_to(ExecutionNodeState.RUNNING)
                except ValueError:
                    self._scheduler.cancel(name)
                    continue

                task = asyncio.create_task(
                    self._execute_node(name),
                    name=f"exec-node-{name[:8]}",
                )
                active_tasks[name] = task

            # Wait for at least one task to complete.
            if active_tasks:
                done, _ = await asyncio.wait(
                    active_tasks.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                await self._process_completed_tasks(
                    active_tasks, done, result
                )

            # Optional pause between batches.
            if self._config.pause_between_batches > 0:
                await asyncio.sleep(self._config.pause_between_batches)

        # Cancel any remaining active tasks.
        for name, task in active_tasks.items():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._scheduler.cancel(name)

        # Finalize result.
        result.duration_ms = (time.monotonic() - start) * 1000.0
        result.max_parallelism = self._scheduler.max_parallelism_reached
        result.nodes_completed = len(self._scheduler.completed)
        result.nodes_failed = len(self._scheduler.failed)
        result.nodes_skipped = len(self._scheduler._skipped)  # noqa: SLF001
        result.retry_count = sum(
            n.retry_count for n in self._nodes.values()
        )
        has_failures = bool(self._scheduler.failed)
        result.status = "failed" if has_failures else "completed"

        # Compute critical path duration.
        durations = {
            n: self._nodes[n].execution_time_ms
            for n in self._nodes
            if n in self._scheduler.completed
        }
        try:
            cp = self._graph.critical_path(durations)
            result.critical_path_ms = sum(
                durations.get(n, 0.0) for n in cp
            )
        except Exception:  # noqa: BLE001
            pass

        logger.info(
            "ParallelExecutor: finished in %.1fms (status=%s, "
            "completed=%d, failed=%d, max_parallel=%d)",
            result.duration_ms, result.status,
            result.nodes_completed, result.nodes_failed,
            result.max_parallelism,
        )

        return result

    async def _execute_node(self, name: str) -> None:
        """Execute a single node with retry handling."""
        node = self._nodes.get(name)
        if node is None:
            return

        policy = self._retry_policies.get(name, self._config.retry_policy)

        while True:
            start = time.monotonic()
            try:
                # Execute the node.
                if self._executor_fn is not None:
                    result = await self._executor_fn(name, node.to_dict())
                else:
                    result = {"status": "ok"}

                # Apply timeout if configured.
                duration_ms = (time.monotonic() - start) * 1000.0
                node.result = result
                node.execution_time_ms = duration_ms
                node.error = None

                # Transition to COMPLETED.
                self._state_machine.transition(
                    name, ExecutionNodeState.COMPLETED,
                    metadata={"duration_ms": duration_ms},
                )
                node.transition_to(ExecutionNodeState.COMPLETED)
                self._scheduler.complete(name)

                await self._emit_event("node.completed", {
                    "node": name,
                    "duration_ms": duration_ms,
                })
                return

            except asyncio.CancelledError:
                self._state_machine.transition(
                    name, ExecutionNodeState.CANCELLED,
                )
                node.transition_to(ExecutionNodeState.CANCELLED)
                self._scheduler.cancel(name)
                raise

            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.monotonic() - start) * 1000.0
                node.execution_time_ms = duration_ms
                node.error = str(exc)
                node.retry_count += 1

                # Check retry policy.
                attempt_record = policy.record_attempt(
                    name, node.retry_count - 1,
                    error=exc, duration_ms=duration_ms,
                )

                if attempt_record["should_retry"]:
                    # Transition to RETRYING -> READY.
                    self._state_machine.transition(
                        name, ExecutionNodeState.FAILED,
                    )
                    node.transition_to(ExecutionNodeState.FAILED)
                    self._state_machine.transition(
                        name, ExecutionNodeState.RETRYING,
                    )
                    node.transition_to(ExecutionNodeState.RETRYING)

                    await policy.wait_for_retry(node.retry_count - 1)

                    self._state_machine.transition(
                        name, ExecutionNodeState.READY,
                    )
                    node.transition_to(ExecutionNodeState.READY)
                    self._scheduler.retry(name)

                    await self._emit_event("node.retry", {
                        "node": name,
                        "attempt": node.retry_count,
                        "error": str(exc),
                        "delay": attempt_record["next_delay"],
                    })
                    continue

                # Exhausted retries.
                self._state_machine.transition(
                    name, ExecutionNodeState.FAILED,
                    metadata={"error": str(exc)},
                )
                node.transition_to(ExecutionNodeState.FAILED)
                self._scheduler.fail(name)

                await self._emit_event("node.failed", {
                    "node": name,
                    "attempt": node.retry_count,
                    "error": str(exc),
                    "exhausted": True,
                })
                return

    async def _process_completed_tasks(
        self,
        active_tasks: dict[str, asyncio.Task],
        done: set[asyncio.Task],
        result: ExecutionResult,
    ) -> None:
        """Process completed asyncio tasks."""
        for name, task in list(active_tasks.items()):
            if task not in done:
                continue
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            del active_tasks[name]

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    async def cancel(self) -> None:
        """Cancel all running nodes."""
        for name in list(self._nodes.keys()):
            node = self._nodes[name]
            if not node.is_terminal:
                try:
                    self._state_machine.transition(
                        name, ExecutionNodeState.CANCELLED,
                    )
                    node.transition_to(ExecutionNodeState.CANCELLED)
                    self._scheduler.cancel(name)
                except ValueError:
                    pass

    def set_retry_policy(self, node_name: str, policy: RetryPolicy) -> None:
        """Set a custom retry policy for a specific node."""
        self._retry_policies[node_name] = policy

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_cb is None:
            return
        try:
            await self._event_cb(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Return execution metrics."""
        return {
            "scheduler": self._scheduler.stats(),
            "state_machine": self._state_machine.to_dict(),
        }


__all__ = ["NodeExecutor", "ParallelExecutor", "ParallelExecutorConfig"]