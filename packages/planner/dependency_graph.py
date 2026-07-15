"""Sprint 3.5 — Dependency Graph System for TaskPlans.

Provides :class:`DependencyGraph`, a directed-graph view over a
:class:`TaskPlan` that supports:

* **Cycle detection** — DFS-based, returns the actual cycle.
* **Topological ordering** — Kahn's algorithm with priority tiebreak.
* **Parallel-batch computation** — groups of steps that can run
  concurrently (every step in batch *N* has all its dependencies in
  batches *0..N-1*).
* **Critical-path analysis** — longest dependency chain, useful for
  estimating minimum total wall-clock time.
* **Ancestor / descendant queries** — transitive closure.
* **Validation** — broken deps, duplicates, isolated steps.

The graph is **plan-backed**: it reads from a :class:`TaskPlan`
instance and stays in sync if the plan's steps are mutated.  Call
:meth:`refresh` after mutating the plan to rebuild the internal
adjacency map.  The graph itself never mutates the plan.

Design notes
------------

* Node identifiers are :attr:`PlanStep.id` strings.
* Edges go from dependency → dependent (``A → B`` means *A must
  finish before B can start*).
* The graph is **decoupled** from the multiagent workflow DAG
  (``packages/multiagent/src/multiagent/workflow/dag.py``).  The
  multiagent DAG is workflow-centric (steps + priorities + scheduler
  hooks); this graph is planner-centric (steps + estimated durations
  + capability hints).  Bridging the two happens in
  :class:`~planner.executor.PlannerExecutor`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .exceptions import CircularDependencyError, DependencyError, GraphError
from .models import PlanStep, TaskPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParallelBatch:
    """A group of steps that can run concurrently.

    Attributes:
        index: 0-based batch index (0 = first batch to run).
        step_ids: Step IDs in this batch (ordered by priority then id).
        estimated_duration: Max ``estimated_duration`` among the
            steps (i.e. the wall-clock time the batch takes if all
            steps run in parallel).  ``0.0`` if any step has no
            estimate.
    """

    index: int = 0
    step_ids: list[str] = field(default_factory=list)
    estimated_duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step_ids": list(self.step_ids),
            "estimated_duration": self.estimated_duration,
        }


@dataclass
class CriticalPath:
    """Result of :meth:`DependencyGraph.critical_path`.

    Attributes:
        step_ids: Ordered list of step IDs forming the longest
            dependency chain.
        total_duration: Sum of ``estimated_duration`` along the path.
            ``0.0`` if any step lacks an estimate.
    """

    step_ids: list[str] = field(default_factory=list)
    total_duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_ids": list(self.step_ids),
            "total_duration": self.total_duration,
        }


@dataclass
class GraphValidationResult:
    """Result of :meth:`DependencyGraph.validate`.

    Attributes:
        ok: ``True`` if the graph has no structural issues.
        cycles: List of cycles (each a list of step IDs).
        broken_deps: List of ``(step_id, missing_dep_id)`` tuples.
        duplicates: List of step IDs that appear more than once.
        isolated: List of step IDs with no deps and no dependents
            (warning only; not an error).
        warnings: Human-readable warning strings.
    """

    ok: bool = True
    cycles: list[list[str]] = field(default_factory=list)
    broken_deps: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    isolated: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "cycles": [list(c) for c in self.cycles],
            "broken_deps": [list(d) for d in self.broken_deps],
            "duplicates": list(self.duplicates),
            "isolated": list(self.isolated),
            "warnings": list(self.warnings),
        }

    @property
    def errors(self) -> list[str]:
        """Flat list of error messages (cycles + broken deps + duplicates)."""
        out: list[str] = []
        for c in self.cycles:
            out.append(f"Circular dependency: {' -> '.join(c)}")
        for sid, dep in self.broken_deps:
            out.append(f"Step '{sid}' depends on missing '{dep}'")
        for d in self.duplicates:
            out.append(f"Duplicate step ID: '{d}'")
        return out


# ---------------------------------------------------------------------------
# DependencyGraph
# ---------------------------------------------------------------------------


class DependencyGraph:
    """Directed graph view over a :class:`TaskPlan`.

    The graph is built lazily from the plan's steps and refreshed
    on demand.  All operations are O(V + E) in the size of the plan.

    Args:
        plan: The backing :class:`TaskPlan`.  The graph reads from
            ``plan.steps`` and never mutates it.
    """

    def __init__(self, plan: TaskPlan) -> None:
        self._plan = plan
        self._adj: dict[str, list[str]] = {}  # dep_id -> [dependent_ids]
        self._reverse: dict[str, list[str]] = {}  # step_id -> [dep_ids]
        self._step_map: dict[str, PlanStep] = {}
        self.refresh()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def plan(self) -> TaskPlan:
        """The backing :class:`TaskPlan`."""
        return self._plan

    @property
    def node_count(self) -> int:
        """Number of nodes (steps) in the graph."""
        return len(self._step_map)

    @property
    def edge_count(self) -> int:
        """Number of directed edges (dependency relations)."""
        return sum(len(v) for v in self._reverse.values())

    # ------------------------------------------------------------------
    # Build / refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the internal adjacency maps from ``plan.steps``.

        Must be called after the plan's steps or their dependencies
        are mutated externally.  Safe to call multiple times.
        """
        self._adj = {}
        self._reverse = {}
        self._step_map = {}

        for step in self._plan.steps:
            # Last-write-wins on duplicate IDs.
            self._step_map[step.id] = step
            self._reverse.setdefault(step.id, [])
            for dep in step.dependencies:
                self._reverse[step.id].append(dep)
                self._adj.setdefault(dep, []).append(step.id)
            # Ensure every step has an adj entry (even if no dependents).
            self._adj.setdefault(step.id, [])

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> GraphValidationResult:
        """Run all structural checks.

        Returns:
            A :class:`GraphValidationResult` with ``ok=True`` if the
            graph is structurally sound.
        """
        result = GraphValidationResult()

        # 1. Duplicate step IDs.
        seen: set[str] = set()
        dups: list[str] = []
        for step in self._plan.steps:
            if step.id in seen and step.id not in dups:
                dups.append(step.id)
            seen.add(step.id)
        result.duplicates = dups
        if dups:
            result.ok = False

        # 2. Broken dependencies (dep_id not in graph).
        for step in self._plan.steps:
            for dep in step.dependencies:
                if dep not in self._step_map:
                    result.broken_deps.append((step.id, dep))
        if result.broken_deps:
            result.ok = False

        # 3. Cycles.
        result.cycles = self._find_cycles()
        if result.cycles:
            result.ok = False

        # 4. Isolated steps (no deps, no dependents).
        for step in self._plan.steps:
            deps = self._reverse.get(step.id, [])
            dependents = self._adj.get(step.id, [])
            if not deps and not dependents and len(self._plan.steps) > 1:
                result.isolated.append(step.id)
                result.warnings.append(
                    f"Step '{step.id}' is isolated (no dependencies, no dependents)"
                )

        return result

    def validate_or_raise(self) -> None:
        """Run :meth:`validate` and raise on the first error.

        Raises:
            DuplicateStepError: On duplicate step IDs.
            DependencyError: On broken dependency references.
            CircularDependencyError: On cycles.
            GraphError: On any other structural issue.
        """
        result = self.validate()
        if result.duplicates:
            from .exceptions import DuplicateStepError

            raise DuplicateStepError(result.duplicates[0])
        if result.broken_deps:
            sid, dep = result.broken_deps[0]
            raise DependencyError(sid, dep)
        if result.cycles:
            raise CircularDependencyError(result.cycles[0])
        if not result.ok:
            raise GraphError("Graph validation failed", node_id=None)

    # ------------------------------------------------------------------
    # Topological ordering
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """Return a topological ordering of step IDs.

        Uses Kahn's algorithm with a stable priority tiebreak
        (priority desc, then step.id asc).

        Raises:
            CircularDependencyError: If a cycle prevents ordering.
        """
        # Build in-degree map (count only existing deps).
        in_degree: dict[str, int] = {
            sid: sum(1 for d in deps if d in self._step_map)
            for sid, deps in self._reverse.items()
        }

        # Seed with zero-indegree nodes, sorted by (priority desc, id asc).
        # PlanStep doesn't carry a priority field; we read an optional
        # metadata hint instead (default 0).
        def _seed_key(sid: str) -> tuple:
            step = self._step_map[sid]
            prio = step.metadata.get("priority", 0)
            if not isinstance(prio, (int, float)):
                prio = 0
            # Negate priority so higher comes first.
            return (-int(prio), sid)

        ready: deque[str] = deque(sorted(
            (sid for sid, deg in in_degree.items() if deg == 0),
            key=_seed_key,
        ))

        ordered: list[str] = []
        while ready:
            node = ready.popleft()
            ordered.append(node)
            # Decrement in-degree of dependents.
            for dependent in sorted(self._adj.get(node, [])):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    # Insert in priority order.
                    ready.append(dependent)

        if len(ordered) != len(self._step_map):
            cycle = self._find_cycles()
            raise CircularDependencyError(cycle[0] if cycle else ["?"])

        return ordered

    # ------------------------------------------------------------------
    # Parallel batches
    # ------------------------------------------------------------------

    def parallel_batches(self) -> list[ParallelBatch]:
        """Group steps into batches that can execute concurrently.

        Batch *i* contains steps whose dependencies are all in
        batches *0..i-1*.  Within a batch, all steps can run in
        parallel because none depends on another.

        Returns:
            A list of :class:`ParallelBatch` instances, ordered by
            execution order (batch 0 first).

        Raises:
            CircularDependencyError: If a cycle prevents batching.
        """
        if not self._step_map:
            return []

        in_degree: dict[str, int] = {
            sid: sum(1 for d in deps if d in self._step_map)
            for sid, deps in self._reverse.items()
        }

        def _step_priority(sid: str) -> int:
            step = self._step_map[sid]
            prio = step.metadata.get("priority", 0)
            if not isinstance(prio, (int, float)):
                return 0
            return int(prio)

        # Initial batch: zero-indegree nodes.
        current_batch = sorted(
            (sid for sid, deg in in_degree.items() if deg == 0),
            key=lambda sid: (-_step_priority(sid), sid),
        )
        batches: list[ParallelBatch] = []
        completed: set[str] = set()

        batch_index = 0
        while current_batch:
            # Compute batch duration = max estimated_duration.
            max_dur = 0.0
            for sid in current_batch:
                step = self._step_map[sid]
                d = float(getattr(step, "estimated_duration", 0.0) or 0.0)
                if d > max_dur:
                    max_dur = d
            batches.append(ParallelBatch(
                index=batch_index,
                step_ids=list(current_batch),
                estimated_duration=max_dur,
            ))
            completed.update(current_batch)
            # Compute next batch.
            next_batch: list[str] = []
            for sid in self._step_map:
                if sid in completed:
                    continue
                deps = self._reverse.get(sid, [])
                if all(d in completed for d in deps if d in self._step_map):
                    next_batch.append(sid)
            next_batch.sort(
                key=lambda sid: (-_step_priority(sid), sid)
            )
            current_batch = next_batch
            batch_index += 1

        if len(completed) != len(self._step_map):
            cycle = self._find_cycles()
            raise CircularDependencyError(cycle[0] if cycle else ["?"])

        return batches

    # ------------------------------------------------------------------
    # Critical path
    # ------------------------------------------------------------------

    def critical_path(self) -> CriticalPath:
        """Return the longest dependency chain in the graph.

        Uses a topological-order DP: for each step, the longest path
        ending at it is its own duration plus the max longest-path
        of its dependencies.

        Returns:
            A :class:`CriticalPath` with the step IDs and the total
            estimated duration.  If durations are all zero, returns
            the path with the most steps.
        """
        if not self._step_map:
            return CriticalPath()

        try:
            order = self.topological_order()
        except CircularDependencyError:
            return CriticalPath()

        # longest[sid] = (duration, path_list)
        longest: dict[str, tuple[float, list[str]]] = {}
        for sid in order:
            step = self._step_map[sid]
            dur = float(getattr(step, "estimated_duration", 0.0) or 0.0)
            best_dur = 0.0
            best_path: list[str] = []
            for dep in self._reverse.get(sid, []):
                if dep not in longest:
                    continue
                dep_dur, dep_path = longest[dep]
                if dep_dur > best_dur:
                    best_dur = dep_dur
                    best_path = dep_path
            longest[sid] = (best_dur + dur, best_path + [sid])

        # Pick the overall max.
        if not longest:
            return CriticalPath()
        max_sid = max(longest, key=lambda s: (longest[s][0], len(longest[s][1])))
        max_dur, max_path = longest[max_sid]
        return CriticalPath(step_ids=max_path, total_duration=max_dur)

    # ------------------------------------------------------------------
    # Ancestors / descendants
    # ------------------------------------------------------------------

    def ancestors(self, step_id: str) -> set[str]:
        """Return all transitive dependencies of *step_id*."""
        self._require_node(step_id)
        result: set[str] = set()
        stack = list(self._reverse.get(step_id, []))
        while stack:
            cur = stack.pop()
            if cur in result or cur not in self._step_map:
                continue
            result.add(cur)
            stack.extend(self._reverse.get(cur, []))
        return result

    def descendants(self, step_id: str) -> set[str]:
        """Return all transitive dependents of *step_id*."""
        self._require_node(step_id)
        result: set[str] = set()
        stack = list(self._adj.get(step_id, []))
        while stack:
            cur = stack.pop()
            if cur in result or cur not in self._step_map:
                continue
            result.add(cur)
            stack.extend(self._adj.get(cur, []))
        return result

    def has_path(self, from_id: str, to_id: str) -> bool:
        """Return ``True`` if there is a path *from_id → ... → to_id*."""
        self._require_node(from_id)
        self._require_node(to_id)
        if from_id == to_id:
            return True
        return to_id in self.descendants(from_id)

    # ------------------------------------------------------------------
    # Ready / blocked queries
    # ------------------------------------------------------------------

    def ready_steps(self, completed: set[str]) -> list[str]:
        """Return step IDs whose dependencies are all in *completed*.

        Args:
            completed: Set of step IDs that have finished (success).

        Returns:
            Sorted list of step IDs ready to run.
        """
        result: list[str] = []
        for sid, deps in self._reverse.items():
            if sid in completed:
                continue
            if all(d in completed for d in deps if d in self._step_map):
                result.append(sid)

        def _step_priority(sid: str) -> int:
            step = self._step_map[sid]
            prio = step.metadata.get("priority", 0)
            if not isinstance(prio, (int, float)):
                return 0
            return int(prio)

        result.sort(key=lambda s: (-_step_priority(s), s))
        return result

    def blocked_steps(self, failed: set[str]) -> list[str]:
        """Return step IDs that cannot run because a dependency failed.

        Args:
            failed: Set of step IDs that have FAILED.

        Returns:
            Sorted list of step IDs that are transitively blocked.
        """
        # A step is blocked if any of its (transitive) dependencies is in failed.
        blocked: set[str] = set()
        # Propagate failure forward through dependents.
        stack = list(failed)
        while stack:
            cur = stack.pop()
            for dependent in self._adj.get(cur, []):
                if dependent not in blocked:
                    blocked.add(dependent)
                    stack.append(dependent)
        return sorted(blocked)

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _find_cycles(self) -> list[list[str]]:
        """Detect all cycles using DFS with 3-colour marking.

        Returns:
            List of cycles (each a list of step IDs).  Empty if no
            cycles.  Cycles are deduplicated by rotation.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = {sid: WHITE for sid in self._step_map}
        path: list[str] = []
        cycles: list[list[str]] = []

        def visit(node: str) -> None:
            colour[node] = GRAY
            path.append(node)
            for dep in self._reverse.get(node, []):
                if dep not in colour:
                    continue  # broken dep, reported elsewhere
                if colour[dep] == GRAY:
                    idx = path.index(dep)
                    cycles.append(list(path[idx:]) + [dep])
                elif colour[dep] == WHITE:
                    visit(dep)
            colour[node] = BLACK
            path.pop()

        for sid in self._step_map:
            if colour[sid] == WHITE:
                visit(sid)

        # Deduplicate by rotation.
        seen: set[tuple[str, ...]] = set()
        unique: list[list[str]] = []
        for c in cycles:
            rotated = c[:-1]
            if not rotated:
                continue
            min_idx = rotated.index(min(rotated))
            normalized = tuple(rotated[min_idx:] + rotated[:min_idx])
            if normalized not in seen:
                seen.add(normalized)
                unique.append(list(normalized) + [normalized[0]])
        return unique

    # ------------------------------------------------------------------
    # Execution plan summary
    # ------------------------------------------------------------------

    def execution_plan_summary(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the execution plan.

        Includes:
        * Topological order.
        * Parallel batches.
        * Critical path.
        * Validation result.
        """
        try:
            topo = self.topological_order()
        except CircularDependencyError as exc:
            topo = [f"<cycle: {exc}>"]

        try:
            batches = self.parallel_batches()
        except CircularDependencyError:
            batches = []

        critical = self.critical_path()
        validation = self.validate()

        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "topological_order": topo,
            "parallel_batches": [b.to_dict() for b in batches],
            "critical_path": critical.to_dict(),
            "validation": validation.to_dict(),
            "max_parallelism": max((len(b.step_ids) for b in batches), default=0),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_node(self, step_id: str) -> None:
        if step_id not in self._step_map:
            raise GraphError(
                f"Step '{step_id}' not found in graph",
                node_id=step_id,
            )


__all__ = [
    "DependencyGraph",
    "ParallelBatch",
    "CriticalPath",
    "GraphValidationResult",
]
