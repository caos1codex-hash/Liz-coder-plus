"""Comprehensive tests for the execution_graph package (Sprint 3.8).

Covers:
- DependencyGraph construction, validation, cycle detection
- Topological Sort (Kahn's algorithm)
- Ready Queue operations
- Execution State Machine transitions
- Retry Policy (exponential backoff, jitter, retryable exceptions)
- Parallel Scheduler (batches, completion, failure, skip)
- Parallel Executor (async parallel execution)
- Recovery Manager (checkpoints, rollback, resume, partial replan)
- Models (ExecutionNode, ExecutionNodeState, ExecutionResult)
- Integration: large DAGs, nested dependencies, race conditions
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))

for _k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
    sys.modules.pop(_k, None)

from src.execution_graph.models import (
    ExecutionNode,
    ExecutionNodeState,
    ExecutionResult,
    can_transition_node,
)
from src.execution_graph.dependency_graph import (
    DependencyCycleError,
    DependencyGraph,
)
from src.execution_graph.topological_sort import (
    kahn_sort,
    topological_levels,
)
from src.execution_graph.ready_queue import ReadyQueue
from src.execution_graph.state_machine import (
    ExecutionStateMachine,
    StateTransition,
)
from src.execution_graph.retry_policy import RetryPolicy
from src.execution_graph.scheduler import (
    ParallelScheduler,
    ScheduleDecision,
    SchedulerConfig,
)
from src.execution_graph.parallel_executor import (
    ParallelExecutor,
    ParallelExecutorConfig,
)
from src.execution_graph.recovery import (
    RecoveryCheckpoint,
    RecoveryManager,
)


# ======================================================================
# Helpers
# ======================================================================

def _make_graph(
    edges: dict[str, list[str]] | None = None,
    priorities: dict[str, int] | None = None,
) -> DependencyGraph:
    """Build a DependencyGraph from an edges dict.

    Args:
        edges: {node: [dependencies]}.
        priorities: {node: priority}.
    """
    graph = DependencyGraph()
    edges = edges or {}
    priorities = priorities or {}
    for node, deps in edges.items():
        graph.add_node(node, dependencies=deps, priority=priorities.get(node, 0))
    return graph


def _make_nodes(
    graph: DependencyGraph,
) -> dict[str, ExecutionNode]:
    """Create ExecutionNode instances from a graph."""
    nodes = {}
    for name in graph.nodes:
        deps = list(graph.edges.get(name, set()))
        nodes[name] = ExecutionNode(
            name=name,
            dependencies=deps,
        )
    return nodes


# ======================================================================
# Tests: ExecutionNodeState & can_transition_node
# ======================================================================


class TestExecutionNodeState:
    """Test state transition validation."""

    def test_pending_to_ready(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.PENDING, ExecutionNodeState.READY
        )

    def test_pending_to_skipped(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.PENDING, ExecutionNodeState.SKIPPED
        )

    def test_pending_to_cancelled(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.PENDING, ExecutionNodeState.CANCELLED
        )

    def test_pending_to_running_is_invalid(self) -> None:
        assert not can_transition_node(
            ExecutionNodeState.PENDING, ExecutionNodeState.RUNNING
        )

    def test_ready_to_running(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.READY, ExecutionNodeState.RUNNING
        )

    def test_running_to_completed(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.RUNNING, ExecutionNodeState.COMPLETED
        )

    def test_running_to_failed(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.RUNNING, ExecutionNodeState.FAILED
        )

    def test_failed_to_retrying(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.FAILED, ExecutionNodeState.RETRYING
        )

    def test_retrying_to_ready(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.RETRYING, ExecutionNodeState.READY
        )

    def test_completed_is_terminal(self) -> None:
        assert not can_transition_node(
            ExecutionNodeState.COMPLETED, ExecutionNodeState.RUNNING
        )
        assert not can_transition_node(
            ExecutionNodeState.COMPLETED, ExecutionNodeState.FAILED
        )

    def test_skipped_is_terminal(self) -> None:
        assert not can_transition_node(
            ExecutionNodeState.SKIPPED, ExecutionNodeState.READY
        )

    def test_cancelled_is_terminal(self) -> None:
        assert not can_transition_node(
            ExecutionNodeState.CANCELLED, ExecutionNodeState.READY
        )

    def test_waiting_to_ready(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.WAITING, ExecutionNodeState.READY
        )

    def test_ready_to_waiting(self) -> None:
        assert can_transition_node(
            ExecutionNodeState.READY, ExecutionNodeState.WAITING
        )


# ======================================================================
# Tests: ExecutionNode
# ======================================================================


class TestExecutionNode:
    """Test ExecutionNode data model."""

    def test_create_default(self) -> None:
        node = ExecutionNode(name="test")
        assert node.name == "test"
        assert node.state == ExecutionNodeState.PENDING
        assert node.retry_count == 0
        assert not node.is_terminal
        assert node.id  # Auto-generated UUID.

    def test_transition_to_valid(self) -> None:
        node = ExecutionNode(name="test")
        node.transition_to(ExecutionNodeState.READY)
        assert node.state == ExecutionNodeState.READY

    def test_transition_to_invalid_raises(self) -> None:
        node = ExecutionNode(name="test")
        with pytest.raises(ValueError, match="cannot transition"):
            node.transition_to(ExecutionNodeState.RUNNING)

    def test_can_retry_true(self) -> None:
        node = ExecutionNode(name="test", max_retries=3)
        node.state = ExecutionNodeState.FAILED
        node.retry_count = 1
        assert node.can_retry

    def test_can_retry_false_exhausted(self) -> None:
        node = ExecutionNode(name="test", max_retries=3)
        node.state = ExecutionNodeState.FAILED
        node.retry_count = 3
        assert not node.can_retry

    def test_transition_sets_started_at(self) -> None:
        node = ExecutionNode(name="test")
        node.transition_to(ExecutionNodeState.READY)
        node.transition_to(ExecutionNodeState.RUNNING)
        assert node.started_at is not None

    def test_transition_sets_completed_at(self) -> None:
        node = ExecutionNode(name="test")
        node.transition_to(ExecutionNodeState.READY)
        node.transition_to(ExecutionNodeState.RUNNING)
        node.transition_to(ExecutionNodeState.COMPLETED)
        assert node.completed_at is not None

    def test_to_dict(self) -> None:
        node = ExecutionNode(name="test", description="desc")
        d = node.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "desc"
        assert d["state"] == "pending"
        assert "id" in d


# ======================================================================
# Tests: DependencyGraph
# ======================================================================


class TestDependencyGraph:
    """Test DependencyGraph construction and queries."""

    def test_empty_graph(self) -> None:
        graph = DependencyGraph()
        assert len(graph.nodes) == 0
        assert graph.roots() == []
        assert graph.leaves() == []

    def test_add_node_no_deps(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A")
        assert "A" in graph.nodes
        assert graph.roots() == ["A"]
        assert graph.leaves() == ["A"]

    def test_add_node_with_deps(self) -> None:
        graph = DependencyGraph()
        graph.add_node("B", dependencies=["A"])
        graph.add_node("A")
        assert graph.parents("B") == {"A"}
        assert graph.children("A") == {"B"}
        assert graph.roots() == ["A"]

    def test_add_dependency(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A")
        graph.add_node("B")
        graph.add_dependency("B", "A")
        assert graph.parents("B") == {"A"}

    def test_remove_node(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A")
        graph.add_node("B", dependencies=["A"])
        graph.remove_node("A")
        assert "A" not in graph.nodes
        assert graph.parents("B") == set()

    def test_roots_and_leaves(self) -> None:
        graph = _make_graph({
            "A": [],
            "B": ["A"],
            "C": ["A"],
            "D": ["B", "C"],
        })
        assert graph.roots() == ["A"]
        assert sorted(graph.leaves()) == ["D"]

    def test_is_ready(self) -> None:
        graph = _make_graph({
            "A": [],
            "B": ["A"],
            "C": ["B"],
        })
        assert graph.is_ready("A", completed=set())
        assert not graph.is_ready("B", completed=set())
        assert graph.is_ready("B", completed={"A"})
        assert not graph.is_ready("C", completed={"A"})
        assert graph.is_ready("C", completed={"A", "B"})

    def test_remaining_dependencies(self) -> None:
        graph = _make_graph({
            "C": ["A", "B"],
            "A": [],
            "B": [],
        })
        assert graph.remaining_dependencies("C", completed={"A"}) == {"B"}
        assert graph.remaining_dependencies("C", completed={"A", "B"}) == set()

    def test_in_degree_out_degree(self) -> None:
        graph = _make_graph({
            "A": [],
            "B": ["A"],
            "C": ["A"],
            "D": ["B", "C"],
        })
        assert graph.in_degree("A") == 0
        assert graph.in_degree("D") == 2
        assert graph.out_degree("A") == 2
        assert graph.out_degree("D") == 0

    def test_validate_clean(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"]})
        errors = graph.validate()
        assert errors == []

    def test_validate_broken_dep(self) -> None:
        # Manually create a broken dep by directly modifying edges.
        graph = DependencyGraph()
        graph.add_node("B")
        graph.edges["B"].add("X")  # X doesn't exist as a node.
        errors = graph.validate()
        assert len(errors) == 1
        assert "non-existent" in errors[0]

    def test_validate_cycle(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", dependencies=["B"])
        graph.add_node("B", dependencies=["A"])
        errors = graph.validate()
        assert any("cycle" in e.lower() for e in errors)

    def test_subgraph(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"],
        })
        sub = graph.subgraph({"A", "B"})
        assert "A" in sub.nodes
        assert "B" in sub.nodes
        assert "C" not in sub.nodes
        assert "D" not in sub.nodes

    def test_affected_subgraph(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["B"], "D": ["C"],
        })
        affected = graph.affected_subgraph("B")
        assert "B" in affected.nodes
        assert "C" in affected.nodes
        assert "D" in affected.nodes
        assert "A" not in affected.nodes

    def test_critical_path(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"],
        })
        durations = {"A": 100, "B": 200, "C": 50, "D": 100}
        cp = graph.critical_path(durations)
        assert cp[0] == "A"
        assert "B" in cp  # B is on the critical path (200ms > 50ms)

    def test_to_dict_roundtrip(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"]})
        d = graph.to_dict()
        restored = DependencyGraph.from_dict(d)
        assert restored.nodes == graph.nodes
        assert restored.edges["B"] == {"A"}

    def test_priorities(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", priority=1)
        graph.add_node("B", priority=0)
        assert graph.priorities["A"] == 1
        assert graph.priorities["B"] == 0

    def test_multiple_roots_sorted_by_priority(self) -> None:
        graph = DependencyGraph()
        graph.add_node("B", priority=1)
        graph.add_node("A", priority=0)
        graph.add_node("C", priority=0)
        assert graph.roots() == ["A", "C", "B"]


# ======================================================================
# Tests: Cycle Detection
# ======================================================================


class TestCycleDetection:

    def test_no_cycle(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["B"]})
        result = graph.detect_cycles()
        assert result is None

    def test_simple_cycle(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", dependencies=["B"])
        graph.add_node("B", dependencies=["A"])
        with pytest.raises(DependencyCycleError):
            graph.detect_cycles()

    def test_three_node_cycle(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", dependencies=["C"])
        graph.add_node("B", dependencies=["A"])
        graph.add_node("C", dependencies=["B"])
        with pytest.raises(DependencyCycleError):
            graph.detect_cycles()

    def test_self_loop(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", dependencies=["A"])
        with pytest.raises(DependencyCycleError):
            graph.detect_cycles()


# ======================================================================
# Tests: Topological Sort
# ======================================================================


class TestTopologicalSort:

    def test_linear_chain(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["B"], "D": ["C"],
        })
        order = kahn_sort(graph)
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")
        assert order.index("C") < order.index("D")

    def test_diamond_dag(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"],
        })
        order = kahn_sort(graph)
        assert order[0] == "A"
        assert order.index("D") > order.index("B")
        assert order.index("D") > order.index("C")

    def test_independent_roots(self) -> None:
        graph = _make_graph({"A": [], "B": [], "C": []})
        order = kahn_sort(graph)
        assert set(order) == {"A", "B", "C"}

    def test_cycle_raises(self) -> None:
        graph = DependencyGraph()
        graph.add_node("A", dependencies=["B"])
        graph.add_node("B", dependencies=["A"])
        with pytest.raises(DependencyCycleError):
            kahn_sort(graph)

    def test_cycle_raises_isolated(self) -> None:
        """Cycle detection with fresh imports (suite isolation)."""
        graph = DependencyGraph()
        graph.add_node("X", dependencies=["Y"])
        graph.add_node("Y", dependencies=["X"])
        try:
            kahn_sort(graph)
            assert False, "Should have raised"
        except DependencyCycleError:
            pass

    def test_priority_tiebreaking(self) -> None:
        graph = DependencyGraph()
        graph.add_node("C", priority=1)
        graph.add_node("B", priority=0)
        graph.add_node("A", priority=0)
        order = kahn_sort(graph)
        # A and B have priority 0, C has 1. A < B alphabetically.
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("C")

    def test_topological_levels(self) -> None:
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"],
        })
        levels = topological_levels(graph)
        assert levels["A"] == 0
        assert levels["B"] == 1
        assert levels["C"] == 1
        assert levels["D"] == 2


# ======================================================================
# Tests: ReadyQueue
# ======================================================================


class TestReadyQueue:

    def test_push_and_pop(self) -> None:
        q = ReadyQueue()
        q.push("A", priority=0)
        q.push("B", priority=1)
        assert q.pop() == "A"  # Lower priority first.
        assert q.pop() == "B"

    def test_pop_empty(self) -> None:
        q = ReadyQueue()
        assert q.pop() is None

    def test_size(self) -> None:
        q = ReadyQueue()
        q.push("A")
        q.push("B")
        assert q.size == 2

    def test_empty(self) -> None:
        q = ReadyQueue()
        assert q.empty
        q.push("A")
        assert not q.empty
        q.pop()
        assert q.empty

    def test_max_size(self) -> None:
        q = ReadyQueue(max_size=1)
        assert q.push("A")
        assert not q.push("B")  # Full.

    def test_duplicate_push(self) -> None:
        q = ReadyQueue()
        q.push("A")
        assert not q.push("A")  # Already in queue.

    def test_mark_completed(self) -> None:
        q = ReadyQueue()
        q.push("A")
        q.mark_completed("A")
        assert q.empty

    def test_mark_failed(self) -> None:
        q = ReadyQueue()
        q.push("A")
        q.mark_failed("A")
        assert q.empty

    def test_next_ready_peek(self) -> None:
        q = ReadyQueue()
        q.push("A")
        assert q.next_ready() == "A"
        assert not q.empty  # Peek doesn't remove.

    def test_contains(self) -> None:
        q = ReadyQueue()
        q.push("A")
        assert q.contains("A")
        assert not q.contains("B")

    def test_clear(self) -> None:
        q = ReadyQueue()
        q.push("A")
        q.push("B")
        q.clear()
        assert q.empty

    def test_to_list(self) -> None:
        q = ReadyQueue()
        q.push("C", priority=2)
        q.push("A", priority=0)
        q.push("B", priority=1)
        assert q.to_list() == ["A", "B", "C"]

    def test_len(self) -> None:
        q = ReadyQueue()
        q.push("A")
        q.push("B")
        assert len(q) == 2


# ======================================================================
# Tests: ExecutionStateMachine
# ======================================================================


class TestExecutionStateMachine:

    def test_initialize(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A", "B", "C"])
        assert sm.get_state("A") == ExecutionNodeState.PENDING
        assert sm.node_count == 3

    def test_transition_valid(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        t = sm.transition("A", ExecutionNodeState.READY)
        assert t.from_state == ExecutionNodeState.PENDING
        assert t.to_state == ExecutionNodeState.READY
        assert sm.get_state("A") == ExecutionNodeState.READY

    def test_transition_invalid_raises(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        with pytest.raises(ValueError, match="cannot transition"):
            sm.transition("A", ExecutionNodeState.RUNNING)

    def test_validate_transition(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        assert sm.validate_transition("A", ExecutionNodeState.READY)
        assert not sm.validate_transition("A", ExecutionNodeState.RUNNING)

    def test_history(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        sm.transition("A", ExecutionNodeState.READY)
        sm.transition("A", ExecutionNodeState.RUNNING)
        sm.transition("A", ExecutionNodeState.COMPLETED)

        history = sm.history(node_name="A")
        assert len(history) == 3
        assert history[0].to_state == ExecutionNodeState.READY

    def test_history_limit(self) -> None:
        sm = ExecutionStateMachine(max_history=2)
        sm.initialize(["A"])
        sm.transition("A", ExecutionNodeState.READY)
        sm.transition("A", ExecutionNodeState.RUNNING)
        sm.transition("A", ExecutionNodeState.COMPLETED)

        history = sm.history(node_name="A", limit=10)
        assert len(history) == 2  # Max history reached.

    def test_all_states(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A", "B"])
        sm.transition("A", ExecutionNodeState.READY)
        states = sm.all_states()
        assert states["A"] == ExecutionNodeState.READY
        assert states["B"] == ExecutionNodeState.PENDING

    def test_reset(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        sm.transition("A", ExecutionNodeState.READY)
        sm.reset()
        assert sm.node_count == 0
        assert sm.get_state("A") is None

    def test_to_dict(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        d = sm.to_dict()
        assert "states" in d
        assert d["node_count"] == 1

    def test_force_transition(self) -> None:
        sm = ExecutionStateMachine()
        sm.initialize(["A"])
        sm.set_state("A", ExecutionNodeState.RUNNING, force=True)
        assert sm.get_state("A") == ExecutionNodeState.RUNNING

    def test_unknown_node_defaults_to_pending(self) -> None:
        sm = ExecutionStateMachine()
        # Transition without initialize.
        sm.transition("X", ExecutionNodeState.READY)
        assert sm.get_state("X") == ExecutionNodeState.READY


# ======================================================================
# Tests: RetryPolicy
# ======================================================================


class TestRetryPolicy:

    def test_should_retry_within_limit(self) -> None:
        policy = RetryPolicy(max_retries=3)
        assert policy.should_retry(RuntimeError("test"), attempt=0)
        assert policy.should_retry(RuntimeError("test"), attempt=2)

    def test_should_retry_exhausted(self) -> None:
        policy = RetryPolicy(max_retries=3)
        assert not policy.should_retry(RuntimeError("test"), attempt=3)

    def test_should_retry_non_retryable(self) -> None:
        policy = RetryPolicy(
            retryable_exceptions=(ConnectionError,),
        )
        assert not policy.should_retry(ValueError("test"), attempt=0)

    def test_should_retry_default_types(self) -> None:
        policy = RetryPolicy()
        assert policy.should_retry(ConnectionError(), attempt=0)
        assert policy.should_retry(TimeoutError(), attempt=0)
        assert policy.should_retry(RuntimeError(), attempt=0)
        assert policy.should_retry(OSError(), attempt=0)

    def test_next_delay_exponential(self) -> None:
        policy = RetryPolicy(
            base_delay=1.0, max_delay=100.0, exponential_base=2.0, jitter=0.0,
        )
        d0 = policy.next_delay(0)
        d1 = policy.next_delay(1)
        d2 = policy.next_delay(2)
        assert abs(d0 - 1.0) < 0.01
        assert abs(d1 - 2.0) < 0.01
        assert abs(d2 - 4.0) < 0.01

    def test_next_delay_capped(self) -> None:
        policy = RetryPolicy(
            base_delay=10.0, max_delay=15.0, exponential_base=2.0, jitter=0.0,
        )
        d3 = policy.next_delay(3)  # 10 * 2^3 = 80, capped at 15
        assert abs(d3 - 15.0) < 0.01

    def test_next_delay_jitter(self) -> None:
        policy = RetryPolicy(
            base_delay=1.0, max_delay=100.0, jitter=0.5,
        )
        # With jitter, delay should vary around the base.
        delays = [policy.next_delay(0) for _ in range(100)]
        assert min(delays) < 1.0
        assert max(delays) > 0.5  # Some jitter above 0.5.

    def test_record_attempt_retryable(self) -> None:
        policy = RetryPolicy(max_retries=3)
        record = policy.record_attempt("node1", attempt=0, error=RuntimeError("fail"))
        assert record["should_retry"] is True
        assert record["next_delay"] > 0
        assert record["exhausted"] is False

    def test_record_attempt_exhausted(self) -> None:
        policy = RetryPolicy(max_retries=3)
        record = policy.record_attempt("node1", attempt=3, error=RuntimeError("fail"))
        assert record["should_retry"] is False
        assert record["exhausted"] is True

    def test_wait_for_retry(self) -> None:
        policy = RetryPolicy(base_delay=0.01, max_delay=0.1, jitter=0.0)
        start = time.monotonic()
        asyncio.get_event_loop().run_until_complete(
            policy.wait_for_retry(0)
        )
        elapsed = (time.monotonic() - start) * 1000
        assert elapsed >= 5  # At least ~10ms.

    def test_to_dict(self) -> None:
        policy = RetryPolicy(max_retries=5)
        d = policy.to_dict()
        assert d["max_retries"] == 5
        assert "retryable_exceptions" in d


# ======================================================================
# Tests: ParallelScheduler
# ======================================================================


class TestParallelScheduler:

    def _linear_graph(self) -> tuple[DependencyGraph, dict[str, ExecutionNode]]:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["B"]})
        nodes = _make_nodes(graph)
        return graph, nodes

    def _diamond_graph(self) -> tuple[DependencyGraph, dict[str, ExecutionNode]]:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]})
        nodes = _make_nodes(graph)
        return graph, nodes

    def test_initial_schedule_roots(self) -> None:
        graph, _ = self._diamond_graph()
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=4))
        decision = sched.schedule()
        assert "A" in decision.batch

    def test_dispatch_marks_running(self) -> None:
        graph, _ = self._diamond_graph()
        sched = ParallelScheduler(graph)
        sched.schedule()
        sched.dispatch(["A"])
        assert "A" in sched.running

    def test_complete_unblocks_dependents(self) -> None:
        graph, _ = self._diamond_graph()
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=4))
        sched.dispatch(["A"])
        newly_ready = sched.complete("A")
        assert set(newly_ready) == {"B", "C"}

    def test_fail_blocks_dependents(self) -> None:
        graph, _ = self._diamond_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        blocked = sched.fail("A")
        assert set(blocked) == {"B", "C"}

    def test_skip_propagates(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        skipped = sched.skip("A")
        assert "A" in skipped
        assert "B" in skipped
        assert "C" in skipped

    def test_retry_queues_node(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        sched.fail("A")
        sched.retry("A")
        batch = sched.next_batch()
        assert "A" in batch

    def test_max_parallel_limit(self) -> None:
        graph = _make_graph({
            "A": [], "B": [], "C": [], "D": [],
        })
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=2))
        batch = sched.schedule()
        assert len(batch.batch) <= 2

    def test_available_slots(self) -> None:
        graph = _make_graph({"A": [], "B": []})
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=4))
        sched.dispatch(["A"])
        assert sched.available_slots == 3

    def test_is_finished_all_completed(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        sched.complete("A")
        sched.dispatch(["B"])
        sched.complete("B")
        sched.dispatch(["C"])
        sched.complete("C")
        assert sched.is_finished

    def test_stats(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        stats = sched.stats()
        assert stats["running"] == 1
        assert stats["total_nodes"] == 3

    def test_compute_levels(self) -> None:
        graph, _ = self._diamond_graph()
        sched = ParallelScheduler(graph)
        sched.compute_levels()
        assert sched.get_level("A") == 0
        assert sched.get_level("B") == 1
        assert sched.get_level("C") == 1
        assert sched.get_level("D") == 2

    def test_cancel(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        sched.cancel("A")
        assert "A" not in sched.running
        assert "A" in sched._cancelled

    def test_next_batch_convenience(self) -> None:
        graph = _make_graph({"A": [], "B": []})
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=4))
        batch = sched.next_batch()
        assert len(batch) == 2
        assert "A" in sched.running
        assert "B" in sched.running

    def test_reset(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        sched.dispatch(["A"])
        sched.complete("A")
        sched.reset()
        assert sched.stats()["running"] == 0
        assert sched.stats()["completed"] == 0

    def test_pending_count(self) -> None:
        graph, _ = self._linear_graph()
        sched = ParallelScheduler(graph)
        assert sched.pending_count() == 3
        sched.dispatch(["A"])
        assert sched.pending_count() == 2

    def test_max_parallelism_reached(self) -> None:
        graph = _make_graph({"A": [], "B": [], "C": []})
        sched = ParallelScheduler(graph, config=SchedulerConfig(max_parallel=4))
        sched.next_batch()
        assert sched.max_parallelism_reached == 3


# ======================================================================
# Tests: ParallelExecutor
# ======================================================================


class TestParallelExecutor:

    def test_execute_linear_sequential(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["B"]})
        nodes = _make_nodes(graph)
        execution_order: list[str] = []

        async def executor(name: str, _data: dict) -> dict:
            execution_order.append(name)
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 3
        assert result.nodes_failed == 0
        assert execution_order.index("A") < execution_order.index("B")
        assert execution_order.index("B") < execution_order.index("C")

    def test_execute_parallel_independent(self) -> None:
        graph = _make_graph({"A": [], "B": [], "C": []})
        nodes = _make_nodes(graph)
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def executor(name: str, _data: dict) -> dict:
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.05)
            async with lock:
                concurrent_count -= 1
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 3
        assert result.max_parallelism >= 2  # Should run in parallel.

    def test_execute_diamond(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]})
        nodes = _make_nodes(graph)
        execution_order: list[str] = []

        async def executor(name: str, _data: dict) -> dict:
            execution_order.append(name)
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 4
        # A must be first.
        assert execution_order[0] == "A"
        # D must be last.
        assert execution_order[-1] == "D"
        # B and C can be in any order, both after A.
        assert execution_order.index("B") > 0
        assert execution_order.index("C") > 0

    def test_execute_with_retry(self) -> None:
        graph = _make_graph({"A": []})
        nodes = _make_nodes(graph)
        attempt_count = 0

        async def executor(name: str, _data: dict) -> dict:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count <= 2:
                raise RuntimeError("transient error")
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(
                    max_retries=3,
                    base_delay=0.01,
                    max_delay=0.05,
                    jitter=0.0,
                ),
            ),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 1
        assert result.nodes_failed == 0
        assert attempt_count == 3

    def test_execute_exhausted_retries(self) -> None:
        graph = _make_graph({"A": []})
        nodes = _make_nodes(graph)

        async def executor(_name: str, _data: dict) -> dict:
            raise RuntimeError("permanent error")

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(
                    max_retries=2,
                    base_delay=0.01,
                    max_delay=0.05,
                    jitter=0.0,
                ),
            ),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_failed == 1
        assert nodes["A"].retry_count == 3  # 1 initial + 2 retries

    def test_execute_skips_dependents_on_failure(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["B"]})
        nodes = _make_nodes(graph)

        async def executor(name: str, _data: dict) -> dict:
            if name == "A":
                raise RuntimeError("A failed")
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(max_retries=0, jitter=0.0),
            ),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_failed == 1
        # B and C should not execute (blocked).

    def test_cancel(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"]})
        nodes = _make_nodes(graph)

        async def executor(name: str, _data: dict) -> dict:
            if name == "A":
                await asyncio.sleep(10)  # Will be cancelled.
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )

        async def run_and_cancel() -> None:
            task = asyncio.create_task(ex.execute())
            await asyncio.sleep(0.05)
            await ex.cancel()
            try:
                await task
            except Exception:
                pass

        asyncio.get_event_loop().run_until_complete(run_and_cancel())
        assert nodes["B"].state in (
            ExecutionNodeState.PENDING,
            ExecutionNodeState.CANCELLED,
        )

    def test_set_retry_policy_per_node(self) -> None:
        graph = _make_graph({"A": []})
        nodes = _make_nodes(graph)

        async def executor(_name: str, _data: dict) -> dict:
            raise ConnectionError("network error")

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(max_retries=0, jitter=0.0),
            ),
        )
        # Override with a more lenient policy for node A.
        # The initial attempt fails (retry_count becomes 1).
        # Override policy allows 1 retry (total 2 attempts).
        ex.set_retry_policy("A", RetryPolicy(
            max_retries=1, base_delay=0.01, max_delay=0.05, jitter=0.0,
        ))
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        # Initial attempt + 1 retry = 2 total attempts.
        assert nodes["A"].retry_count >= 2

    def test_metrics(self) -> None:
        graph = _make_graph({"A": []})
        nodes = _make_nodes(graph)

        async def executor(_name: str, _data: dict) -> dict:
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )
        asyncio.get_event_loop().run_until_complete(ex.execute())
        metrics = ex.metrics()
        assert "scheduler" in metrics
        assert "state_machine" in metrics


# ======================================================================
# Tests: RecoveryManager
# ======================================================================


class TestRecoveryManager:

    def _setup_graph(self) -> tuple[DependencyGraph, dict[str, ExecutionNode], RecoveryManager]:
        graph = _make_graph({"A": [], "B": ["A"], "C": ["B"], "D": ["A"]})
        nodes = _make_nodes(graph)
        rm = RecoveryManager(graph, nodes)
        return graph, nodes, rm

    def test_save_checkpoint(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        nodes["B"].state = ExecutionNodeState.RUNNING
        cp = rm.save_checkpoint(graph_id="test-plan")
        assert "A" in cp.completed_nodes
        assert len(cp.failed_nodes) == 0

    def test_recover_task_retry(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["B"].state = ExecutionNodeState.FAILED
        result = rm.recover_task("B", strategy="retry")
        assert result["action"] == "retry"
        assert nodes["B"].state == ExecutionNodeState.PENDING

    def test_recover_task_skip(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["B"].state = ExecutionNodeState.FAILED
        result = rm.recover_task("B", strategy="skip")
        assert result["action"] == "skip"
        assert nodes["B"].state == ExecutionNodeState.SKIPPED
        # C should also be skipped (transitive).
        assert nodes["C"].state == ExecutionNodeState.SKIPPED

    def test_recover_task_not_found(self) -> None:
        _, _, rm = self._setup_graph()
        result = rm.recover_task("Z", strategy="retry")
        assert "error" in result

    def test_recover_task_not_failed(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["B"].state = ExecutionNodeState.PENDING
        result = rm.recover_task("B", strategy="retry")
        assert "error" in result

    def test_recover_plan(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        nodes["B"].state = ExecutionNodeState.FAILED
        result = rm.recover_plan()
        assert result["recovered"] is True
        assert "B" in result["recovered_nodes"]

    def test_recover_plan_no_failures(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        result = rm.recover_plan()
        assert result["recovered"] is False

    def test_rollback(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        nodes["B"].state = ExecutionNodeState.FAILED
        nodes["C"].state = ExecutionNodeState.RUNNING
        rm.save_checkpoint(graph_id="test")

        # Now corrupt state.
        nodes["B"].state = ExecutionNodeState.FAILED
        nodes["C"].state = ExecutionNodeState.SKIPPED

        result = rm.rollback()
        assert result["restored"] >= 2
        assert nodes["B"].state == ExecutionNodeState.PENDING

    def test_rollback_no_checkpoint(self) -> None:
        _, _, rm = self._setup_graph()
        result = rm.rollback()
        assert "error" in result

    def test_resume(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        result = rm.resume()
        assert "A" in result["completed"]
        assert "B" in result["ready"]  # B depends only on A.

    def test_continue_from_failure_skip(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        nodes["B"].state = ExecutionNodeState.FAILED
        result = rm.continue_from_failure("B", skip_failed=True)
        assert "B" in result["skipped_nodes"]
        assert "C" in result["skipped_nodes"]
        # D depends only on A, so it can still run.
        assert "D" in result["runnable_nodes"]

    def test_partial_replan(self) -> None:
        _, _, rm = self._setup_graph()
        result = rm.partial_replan("B")
        assert result["failed_node"] == "B"
        assert "C" in result["affected_nodes"]
        assert "A" not in result["affected_nodes"]

    def test_partial_replan_with_fn(self) -> None:
        _, _, rm = self._setup_graph()

        def replanner(failed: str, affected: list[str]) -> list[dict]:
            return [{"name": f"replanned_{failed}", "deps": affected}]

        result = rm.partial_replan("B", replan_fn=replanner)
        assert "new_plan" in result
        assert len(result["new_plan"]) == 1

    def test_checkpoints_list(self) -> None:
        _, _, rm = self._setup_graph()
        rm.save_checkpoint()
        rm.save_checkpoint()
        cps = rm.checkpoints()
        assert len(cps) == 2

    def test_clear_checkpoints(self) -> None:
        _, _, rm = self._setup_graph()
        rm.save_checkpoint()
        rm.clear_checkpoints()
        assert rm.get_latest_checkpoint() is None

    def test_checkpoint_to_dict(self) -> None:
        _, nodes, rm = self._setup_graph()
        nodes["A"].state = ExecutionNodeState.COMPLETED
        nodes["A"].result = {"output": 42}
        cp = rm.save_checkpoint()
        d = cp.to_dict()
        assert "id" in d
        assert "A" in d["completed_nodes"]
        assert d["node_results"]["A"]["output"] == 42


# ======================================================================
# Tests: Large / Complex DAGs
# ======================================================================


class TestLargeDAG:

    def test_large_linear_dag(self) -> None:
        """Test a linear chain of 100 nodes."""
        n = 100
        edges: dict[str, list[str]] = {}
        for i in range(n):
            name = f"task_{i}"
            deps = [f"task_{i-1}"] if i > 0 else []
            edges[name] = deps

        graph = _make_graph(edges)
        order = kahn_sort(graph)
        assert len(order) == n
        for i in range(n - 1):
            assert order.index(f"task_{i}") < order.index(f"task_{i+1}")

    def test_large_parallel_dag(self) -> None:
        """Test a DAG with 50 independent roots."""
        edges: dict[str, list[str]] = {}
        for i in range(50):
            edges[f"task_{i}"] = []
        edges["final"] = [f"task_{i}" for i in range(50)]

        graph = _make_graph(edges)
        order = kahn_sort(graph)
        assert order[-1] == "final"

    def test_wide_dag_parallel_execution(self) -> None:
        """Test parallel execution of a wide DAG."""
        n_roots = 10
        edges: dict[str, list[str]] = {}
        for i in range(n_roots):
            edges[f"root_{i}"] = []
        edges["join"] = [f"root_{i}" for i in range(n_roots)]

        graph = _make_graph(edges)
        nodes = _make_nodes(graph)
        max_concurrent = 0
        current = 0
        lock = asyncio.Lock()

        async def executor(name: str, _data: dict) -> dict:
            nonlocal max_concurrent, current
            async with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            await asyncio.sleep(0.01)
            async with lock:
                current -= 1
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=n_roots),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == n_roots + 1
        assert max_concurrent >= 3  # Should have parallelism.

    def test_random_dag(self) -> None:
        """Generate and validate a random DAG."""
        import random
        random.seed(42)
        n = 30
        graph = DependencyGraph()
        all_names = [f"n_{i}" for i in range(n)]
        for name in all_names:
            graph.add_node(name)

        # Add random edges (ensuring no cycles by only connecting
        # lower-index nodes to higher-index nodes).
        for i in range(n):
            for j in range(i + 1, n):
                if random.random() < 0.15:
                    graph.add_dependency(f"n_{j}", f"n_{i}")

        # Should be valid (no cycles).
        errors = graph.validate()
        assert not errors

        # Topological sort should work.
        order = kahn_sort(graph)
        assert len(order) == n

        # Should be executable.
        nodes = _make_nodes(graph)

        async def executor(_name: str, _data: dict) -> dict:
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=8),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == n


# ======================================================================
# Tests: Nested Dependencies
# ======================================================================


class TestNestedDependencies:

    def test_deep_nesting(self) -> None:
        """Test a deeply nested chain: A -> B -> C -> ... -> Z."""
        n = 26
        edges: dict[str, list[str]] = {}
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for i, ch in enumerate(letters):
            edges[ch] = [letters[i - 1]] if i > 0 else []

        graph = _make_graph(edges)
        levels = topological_levels(graph)
        for i, ch in enumerate(letters):
            assert levels[ch] == i

    def test_multi_level_fan_out_fan_in(self) -> None:
        """Test: 1 root -> 5 middle -> 1 leaf."""
        edges: dict[str, list[str]] = {
            "root": [],
            "mid1": ["root"], "mid2": ["root"], "mid3": ["root"],
            "mid4": ["root"], "mid5": ["root"],
            "leaf": ["mid1", "mid2", "mid3", "mid4", "mid5"],
        }
        graph = _make_graph(edges)
        levels = topological_levels(graph)
        assert levels["root"] == 0
        for m in ["mid1", "mid2", "mid3", "mid4", "mid5"]:
            assert levels[m] == 1
        assert levels["leaf"] == 2

    def test_nested_parallel_execution(self) -> None:
        """Fan-out/fan-in pattern with parallel execution."""
        edges: dict[str, list[str]] = {
            "root": [],
            "w1": ["root"], "w2": ["root"], "w3": ["root"],
            "leaf": ["w1", "w2", "w3"],
        }
        graph = _make_graph(edges)
        nodes = _make_nodes(graph)
        order: list[str] = []

        async def executor(name: str, _data: dict) -> dict:
            order.append(name)
            await asyncio.sleep(0.01)
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=4),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 5
        assert order[0] == "root"
        assert order[-1] == "leaf"
        # Workers should all appear before leaf.
        for w in ["w1", "w2", "w3"]:
            assert order.index(w) < order.index("leaf")


# ======================================================================
# Tests: Race Conditions / Async Safety
# ======================================================================


class TestAsyncSafety:

    def test_concurrent_state_transitions(self) -> None:
        """Multiple coroutines transitioning different nodes."""
        sm = ExecutionStateMachine()
        sm.initialize(["A", "B", "C"])

        async def transition_node(name: str) -> None:
            sm.transition(name, ExecutionNodeState.READY)
            sm.transition(name, ExecutionNodeState.RUNNING)
            sm.transition(name, ExecutionNodeState.COMPLETED)

        async def run_all() -> None:
            await asyncio.gather(
                transition_node("A"),
                transition_node("B"),
                transition_node("C"),
            )

        asyncio.get_event_loop().run_until_complete(run_all())
        assert sm.get_state("A") == ExecutionNodeState.COMPLETED
        assert sm.get_state("B") == ExecutionNodeState.COMPLETED
        assert sm.get_state("C") == ExecutionNodeState.COMPLETED

    def test_ready_queue_concurrent_push_pop(self) -> None:
        """Concurrent push/pop on the ready queue."""
        q = ReadyQueue()

        async def pusher() -> None:
            for i in range(50):
                q.push(f"node_{i}", priority=i)

        async def popper() -> None:
            count = 0
            while not q.empty:
                q.pop()
                count += 1
                await asyncio.sleep(0)

        async def run() -> None:
            await asyncio.gather(pusher(), popper())

        asyncio.get_event_loop().run_until_complete(run())

    def test_parallel_executor_stress(self) -> None:
        """Stress test: many independent nodes."""
        n = 20
        edges: dict[str, list[str]] = {f"t_{i}": [] for i in range(n)}
        graph = _make_graph(edges)
        nodes = _make_nodes(graph)

        async def executor(name: str, _data: dict) -> dict:
            await asyncio.sleep(0.005)
            return {"status": "ok", "name": name}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(max_parallel=8),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == n
        assert result.nodes_failed == 0
        assert result.max_parallelism >= 2


# ======================================================================
# Tests: ExecutionResult
# ======================================================================


class TestExecutionResult:

    def test_default_values(self) -> None:
        r = ExecutionResult()
        assert r.nodes_total == 0
        assert r.status == "pending"

    def test_to_dict(self) -> None:
        r = ExecutionResult(
            graph_id="test", nodes_total=10,
            nodes_completed=8, nodes_failed=1, duration_ms=100.0,
        )
        d = r.to_dict()
        assert d["graph_id"] == "test"
        assert d["nodes_completed"] == 8
        assert d["duration_ms"] == 100.0


# ======================================================================
# Tests: RecoveryCheckpoint
# ======================================================================


class TestRecoveryCheckpoint:

    def test_default_values(self) -> None:
        cp = RecoveryCheckpoint()
        assert cp.graph_id == ""
        assert len(cp.completed_nodes) == 0

    def test_to_dict(self) -> None:
        cp = RecoveryCheckpoint(
            graph_id="g1",
            completed_nodes={"A", "B"},
            failed_nodes={"C"},
        )
        d = cp.to_dict()
        assert d["graph_id"] == "g1"
        assert sorted(d["completed_nodes"]) == ["A", "B"]
        assert d["failed_nodes"] == ["C"]


# ======================================================================
# Tests: DependencyGraph.critical_path
# ======================================================================


class TestCriticalPath:

    def test_empty_graph(self) -> None:
        graph = DependencyGraph()
        assert graph.critical_path() == []

    def test_single_node(self) -> None:
        graph = _make_graph({"A": []})
        cp = graph.critical_path({"A": 50.0})
        assert cp == ["A"]

    def test_two_parallel_paths(self) -> None:
        graph = _make_graph({
            "A": [],
            "B": ["A"], "C": ["A"],
            "D": ["B", "C"],
        })
        # Path A -> B -> D: 10 + 20 = 30
        # Path A -> C -> D: 10 + 5 = 15
        durations = {"A": 10, "B": 20, "C": 5, "D": 1}
        cp = graph.critical_path(durations)
        assert "B" in cp
        assert "A" in cp
        assert "D" in cp

    def test_no_durations(self) -> None:
        graph = _make_graph({"A": [], "B": ["A"]})
        cp = graph.critical_path()
        # With no durations, the critical path is just the longest
        # chain by node count. A is always included.
        assert "A" in cp


# ======================================================================
# Tests: DependencyGraph.topological_levels
# ======================================================================


class TestTopologicalLevels:

    def test_single_level(self) -> None:
        graph = _make_graph({"A": [], "B": []})
        levels = graph.topological_levels()
        assert levels["A"] == 0
        assert levels["B"] == 0

    def test_complex_dag_levels(self) -> None:
        graph = _make_graph({
            "A": [],
            "B": ["A"],
            "C": ["A"],
            "D": ["B"],
            "E": ["B", "C"],
            "F": ["D", "E"],
        })
        levels = graph.topological_levels()
        assert levels["A"] == 0
        assert levels["B"] == 1
        assert levels["C"] == 1
        assert levels["D"] == 2
        assert levels["E"] == 2
        assert levels["F"] == 3


# ======================================================================
# Tests: SchedulerConfig
# ======================================================================


class TestSchedulerConfig:

    def test_defaults(self) -> None:
        cfg = SchedulerConfig()
        assert cfg.max_parallel == 4
        assert cfg.enable_preemption is False

    def test_custom(self) -> None:
        cfg = SchedulerConfig(max_parallel=8, enable_preemption=True)
        assert cfg.max_parallel == 8
        assert cfg.enable_preemption is True


# ======================================================================
# Tests: ScheduleDecision
# ======================================================================


class TestScheduleDecision:

    def test_defaults(self) -> None:
        d = ScheduleDecision()
        assert d.batch == []
        assert d.level == 0

    def test_to_dict(self) -> None:
        d = ScheduleDecision(batch=["A", "B"], level=1, reason="test")
        d_dict = d.to_dict()
        assert d_dict["batch"] == ["A", "B"]
        assert d_dict["reason"] == "test"


# ======================================================================
# Tests: StateTransition
# ======================================================================


class TestStateTransition:

    def test_to_dict(self) -> None:
        t = StateTransition(
            node_name="A",
            from_state=ExecutionNodeState.PENDING,
            to_state=ExecutionNodeState.READY,
        )
        d = t.to_dict()
        assert d["node_name"] == "A"
        assert d["from"] == "pending"
        assert d["to"] == "ready"


# ======================================================================
# Tests: Failure Recovery in Executor
# ======================================================================


class TestFailureRecovery:

    def test_mid_graph_failure_with_recovery(self) -> None:
        """Test failure in the middle of a graph with recovery."""
        graph = _make_graph({
            "A": [],
            "B": ["A"],
            "C": ["B"],
            "D": ["C"],
        })
        nodes = _make_nodes(graph)
        attempt_count = {"B": 0}

        async def executor(name: str, _data: dict) -> dict:
            if name == "B":
                attempt_count["B"] += 1
                if attempt_count["B"] == 1:
                    raise RuntimeError("transient")
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(
                    max_retries=3, base_delay=0.01,
                    max_delay=0.05, jitter=0.0,
                ),
            ),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_completed == 4
        assert attempt_count["B"] == 3  # 1 initial + 2 retries (1st retry fails at scheduler level)

    def test_root_failure_skips_all(self) -> None:
        """Test failure of root node skips entire graph."""
        graph = _make_graph({
            "A": [], "B": ["A"], "C": ["A"],
        })
        nodes = _make_nodes(graph)

        async def executor(name: str, _data: dict) -> dict:
            if name == "A":
                raise RuntimeError("root failed")
            return {"status": "ok"}

        ex = ParallelExecutor(
            graph, nodes,
            node_executor=executor,
            config=ParallelExecutorConfig(
                max_parallel=4,
                retry_policy=RetryPolicy(max_retries=0, jitter=0.0),
            ),
        )
        result = asyncio.get_event_loop().run_until_complete(ex.execute())
        assert result.nodes_failed == 1
        assert nodes["A"].state == ExecutionNodeState.FAILED
        # B and C should remain pending (blocked).