"""Directed Acyclic Graph for the planner (Sprint 2.8).

The :class:`DAG` is the structural backbone of an
:class:`~planner.execution_plan.ExecutionPlan`. It stores tasks as nodes
and dependencies as directed edges ``parent -> child`` (where ``child``
depends on ``parent``).

Guarantees:

- **Acyclic by construction**: ``add_dependency`` rejects any edge that
  would introduce a cycle, raising
  :class:`~planner.exceptions.CycleDetectedError`.
- **Serializable**: ``to_dict`` / ``from_dict`` round-trip safely.
- **Layered execution**: :meth:`parallel_layers` returns groups of
  tasks that can be executed concurrently.
- **Critical path**: :meth:`critical_path` returns the longest chain
  (by summed estimated duration), which determines the minimum makespan.

The implementation uses Kahn's algorithm for topological sorting and
DFS with 3-color marking for cycle detection. All operations are
O(V + E) or better.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Any

from .exceptions import CycleDetectedError, DependencyError

# ----------------------------------------------------------------------
# DAG
# ----------------------------------------------------------------------


class DAG:
    """Directed Acyclic Graph of task ids.

    The graph is stored as an adjacency list. ``_edges[parent]`` is the
    set of children that depend on ``parent``. ``_reverse[child]`` is
    the set of parents that ``child`` depends on.

    Args:
        tasks: Optional iterable of task ids to seed the graph with.
    """

    def __init__(self, tasks: Iterable[str] | None = None) -> None:
        # Forward edges: parent -> {children}
        self._edges: dict[str, set[str]] = defaultdict(set)
        # Reverse edges: child -> {parents}
        self._reverse: dict[str, set[str]] = defaultdict(set)
        # All known node ids (so isolated nodes survive serialization).
        self._nodes: set[str] = set()
        if tasks:
            for t in tasks:
                self.add_task(t)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_task(self, task_id: str) -> None:
        """Add an isolated node to the graph.

        Idempotent: adding the same id twice is a no-op.

        Raises:
            ValueError: if ``task_id`` is empty.
        """
        if not task_id:
            raise ValueError("task_id cannot be empty")
        self._nodes.add(task_id)
        # Touch adjacency entries so iteration is stable.
        self._edges.setdefault(task_id, set())
        self._reverse.setdefault(task_id, set())

    def remove_task(self, task_id: str) -> None:
        """Remove a node and all edges incident to it.

        Idempotent: removing a non-existent id is a no-op.
        """
        if task_id not in self._nodes:
            return
        # Remove from forward edges.
        for child in list(self._edges.get(task_id, set())):
            self._reverse[child].discard(task_id)
        # Remove from reverse edges.
        for parent in list(self._reverse.get(task_id, set())):
            self._edges[parent].discard(task_id)
        self._edges.pop(task_id, None)
        self._reverse.pop(task_id, None)
        self._nodes.discard(task_id)

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Add an edge ``depends_on -> task_id`` (``task_id`` depends on ``depends_on``).

        Raises:
            ValueError: if either id is empty or if ``task_id == depends_on``.
            CycleDetectedError: if the edge would introduce a cycle.
        """
        if not task_id or not depends_on:
            raise ValueError("task_id and depends_on cannot be empty")
        if task_id == depends_on:
            raise ValueError("a task cannot depend on itself")
        # Ensure both nodes exist.
        self.add_task(task_id)
        self.add_task(depends_on)
        # If the edge already exists, no-op.
        if depends_on in self._reverse[task_id]:
            return
        # Tentatively add the edge and check for cycles.
        self._edges[depends_on].add(task_id)
        self._reverse[task_id].add(depends_on)
        cycle = self._find_cycle_through(task_id)
        if cycle:
            # Roll back.
            self._edges[depends_on].discard(task_id)
            self._reverse[task_id].discard(depends_on)
            raise CycleDetectedError(cycle)

    def remove_dependency(self, task_id: str, depends_on: str) -> None:
        """Remove the edge ``depends_on -> task_id`` if present.

        Idempotent.
        """
        self._edges.get(depends_on, set()).discard(task_id)
        self._reverse.get(task_id, set()).discard(depends_on)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def has_task(self, task_id: str) -> bool:
        """True if ``task_id`` is a node in the graph."""
        return task_id in self._nodes

    def __contains__(self, task_id: object) -> bool:
        return isinstance(task_id, str) and task_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def task_ids(self) -> list[str]:
        """Return all node ids (sorted for determinism)."""
        return sorted(self._nodes)

    def parents(self, task_id: str) -> list[str]:
        """Return the direct parents of ``task_id`` (sorted)."""
        if task_id not in self._nodes:
            raise KeyError(task_id)
        return sorted(self._reverse.get(task_id, set()))

    def children(self, task_id: str) -> list[str]:
        """Return the direct children of ``task_id`` (sorted)."""
        if task_id not in self._nodes:
            raise KeyError(task_id)
        return sorted(self._edges.get(task_id, set()))

    def roots(self) -> list[str]:
        """Return nodes with no parents (sorted)."""
        return sorted(n for n in self._nodes if not self._reverse.get(n, set()))

    def leaves(self) -> list[str]:
        """Return nodes with no children (sorted)."""
        return sorted(n for n in self._nodes if not self._edges.get(n, set()))

    def ancestors(self, task_id: str) -> list[str]:
        """Return all transitive parents of ``task_id`` (sorted)."""
        if task_id not in self._nodes:
            raise KeyError(task_id)
        seen: set[str] = set()
        stack = list(self._reverse.get(task_id, set()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self._reverse.get(current, set()))
        return sorted(seen)

    def descendants(self, task_id: str) -> list[str]:
        """Return all transitive children of ``task_id`` (sorted)."""
        if task_id not in self._nodes:
            raise KeyError(task_id)
        seen: set[str] = set()
        stack = list(self._edges.get(task_id, set()))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(self._edges.get(current, set()))
        return sorted(seen)

    def has_path(self, from_id: str, to_id: str) -> bool:
        """True if there is a directed path ``from_id -> ... -> to_id``."""
        if from_id == to_id:
            return from_id in self._nodes
        return to_id in set(self.descendants(from_id))

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def detect_cycles(self) -> list[list[str]]:
        """Return all distinct cycles in the graph.

        Uses DFS with 3-color marking (white/gray/black). Each cycle is
        returned as a list of node ids in the order they appear in the
        cycle, with the first node repeated at the end.

        Returns:
            A list of cycles (each a list of node ids). Empty if acyclic.
        """
        white, gray, black = 0, 1, 2
        color: dict[str, int] = {n: white for n in self._nodes}
        cycles: list[list[str]] = []
        path: list[str] = []

        def visit(node: str) -> None:
            color[node] = gray
            path.append(node)
            for child in sorted(self._edges.get(node, set())):
                if color.get(child) == gray:
                    idx = path.index(child)
                    cycles.append(list(path[idx:]) + [child])
                elif color.get(child) == white:
                    visit(child)
            color[node] = black
            path.pop()

        for n in sorted(self._nodes):
            if color[n] == white:
                visit(n)

        # Deduplicate by canonical rotation.
        return _dedupe_cycles(cycles)

    def _find_cycle_through(self, task_id: str) -> list[str]:
        """Return the cycle that would be created by adding an edge to ``task_id``.

        Returns an empty list if no cycle is introduced.
        """
        # A cycle exists iff there is already a path from task_id back
        # to one of its new parents. We've already tentatively added
        # the edge, so just run detect_cycles and find one containing
        # task_id.
        for cycle in self.detect_cycles():
            if task_id in cycle:
                return cycle
        return []

    # ------------------------------------------------------------------
    # Topological sort
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """Return a topological ordering of the nodes (Kahn's algorithm).

        Raises:
            CycleDetectedError: if the graph contains a cycle.
        """
        in_degree: dict[str, int] = {n: len(self._reverse.get(n, set())) for n in self._nodes}
        # Stable queue: process roots alphabetically, then add new
        # roots alphabetically as they appear.
        ready: deque[str] = deque(sorted(n for n, deg in in_degree.items() if deg == 0))
        result: list[str] = []
        while ready:
            node = ready.popleft()
            result.append(node)
            for child in sorted(self._edges.get(node, set())):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    # Insert in sorted order.
                    _insort_deque(ready, child)
        if len(result) != len(self._nodes):
            cycles = self.detect_cycles()
            raise CycleDetectedError(cycles[0] if cycles else [])
        return result

    # ------------------------------------------------------------------
    # Parallel layers
    # ------------------------------------------------------------------

    def parallel_layers(self) -> list[list[str]]:
        """Group nodes into layers that can be executed concurrently.

        Layer 0 contains all roots. Layer ``i`` contains nodes whose
        parents are all in layers ``< i``. Within each layer, nodes
        are sorted alphabetically for determinism.

        Raises:
            CycleDetectedError: if the graph contains a cycle.
        """
        if not self._nodes:
            return []
        # Detect cycles up front for a clearer error.
        cycles = self.detect_cycles()
        if cycles:
            raise CycleDetectedError(cycles[0])

        # Compute the longest path from any root to each node.
        # A node's layer is max(parent_layers) + 1, or 0 if it's a root.
        layer_of: dict[str, int] = {}
        # Process in topological order so parents are always computed first.
        for node in self.topological_sort():
            parents = self._reverse.get(node, set())
            if not parents:
                layer_of[node] = 0
            else:
                layer_of[node] = max(layer_of[p] for p in parents) + 1
        # Group by layer.
        max_layer = max(layer_of.values()) if layer_of else 0
        layers: list[list[str]] = [
            sorted(n for n, layer in layer_of.items() if layer == i) for i in range(max_layer + 1)
        ]
        return layers

    # ------------------------------------------------------------------
    # Critical path
    # ------------------------------------------------------------------

    def critical_path(
        self,
        durations: dict[str, float] | None = None,
    ) -> list[str]:
        """Return the critical (longest) path through the graph.

        The critical path is the chain of tasks whose total estimated
        duration is maximal. It determines the minimum makespan of the
        plan.

        Args:
            durations: Optional ``{task_id: seconds}`` mapping. If
                omitted, every task is assumed to have duration 1.0.

        Returns:
            A list of task ids forming the critical path, from a root
            to a leaf.

        Raises:
            CycleDetectedError: if the graph contains a cycle.
        """
        if not self._nodes:
            return []
        cycles = self.detect_cycles()
        if cycles:
            raise CycleDetectedError(cycles[0])
        d = durations or {}
        # longest[v] = duration(v) + max(longest[u] for u in parents(v))
        # Process in topological order so parents are computed first.
        longest: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}
        for node in self.topological_sort():
            node_dur = float(d.get(node, 1.0))
            parents = self._reverse.get(node, set())
            if not parents:
                longest[node] = node_dur
                predecessor[node] = None
            else:
                best_parent = max(parents, key=lambda p: longest.get(p, 0.0))
                longest[node] = node_dur + longest.get(best_parent, 0.0)
                predecessor[node] = best_parent
        # The critical path ends at the node with the maximum longest[].
        end = max(longest, key=lambda n: longest[n])
        # Reconstruct the path by walking predecessors.
        path: list[str] = []
        cursor: str | None = end
        while cursor is not None:
            path.append(cursor)
            cursor = predecessor[cursor]
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors.

        An empty list means the graph is structurally valid. The checks
        performed are:

        - No cycles.
        - No broken edges (edges always reference known nodes by
          construction, so this is a defensive check).
        """
        errors: list[str] = []
        cycles = self.detect_cycles()
        if cycles:
            for c in cycles:
                errors.append(f"Cycle: {' -> '.join(c)}")
        # Broken edges should not happen because add_dependency auto-
        # creates nodes, but check anyway for deserialized graphs.
        for parent, children in self._edges.items():
            if parent not in self._nodes:
                errors.append(f"Edge from unknown node '{parent}'")
            for child in children:
                if child not in self._nodes:
                    errors.append(f"Edge to unknown node '{child}' (from '{parent}')")
        return errors

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Only edges are stored (nodes with no edges are still listed in
        ``nodes`` to preserve isolated tasks).
        """
        return {
            "nodes": sorted(self._nodes),
            "edges": [
                {"from": parent, "to": child}
                for parent in sorted(self._edges)
                for child in sorted(self._edges[parent])
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAG:
        """Reconstruct a :class:`DAG` from :meth:`to_dict` output.

        Raises:
            CycleDetectedError: if the deserialized graph contains a cycle.
        """
        dag = cls()
        for node in data.get("nodes", []):
            dag.add_task(node)
        for edge in data.get("edges", []):
            dag.add_dependency(edge["to"], edge["from"])
        return dag

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def copy(self) -> DAG:
        """Return a shallow copy of this graph (edges are copied)."""
        new = DAG()
        new._nodes = set(self._nodes)
        new._edges = {k: set(v) for k, v in self._edges.items()}
        new._reverse = {k: set(v) for k, v in self._reverse.items()}
        return new

    def __repr__(self) -> str:
        return f"DAG(nodes={len(self._nodes)}, edges={sum(len(v) for v in self._edges.values())})"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _insort_deque(d: deque[str], item: str) -> None:
    """Insert ``item`` into deque ``d`` maintaining sorted order."""
    # deque doesn't support bisect directly; convert to list, insort,
    # then rebuild. For small deques (typical scheduler load) this is
    # faster than calling sorted() on every pop.
    items = list(d)
    items.append(item)
    items.sort()
    d.clear()
    d.extend(items)


def _dedupe_cycles(cycles: list[list[str]]) -> list[list[str]]:
    """Return ``cycles`` with duplicates removed.

    Two cycles are duplicates if they contain the same set of nodes
    in the same cyclic order (allowing rotation).
    """
    seen: set[tuple[str, ...]] = set()
    unique: list[list[str]] = []
    for cycle in cycles:
        if not cycle:
            continue
        # Drop the trailing duplicate (last == first) for normalization.
        body = cycle[:-1] if cycle[-1] == cycle[0] else cycle
        if not body:
            continue
        # Rotate so the smallest id is first.
        min_idx = body.index(min(body))
        rotated = tuple(body[min_idx:] + body[:min_idx])
        if rotated in seen:
            continue
        seen.add(rotated)
        unique.append(list(rotated) + [rotated[0]])
    return unique


def validate_no_cycles(dag: DAG) -> None:
    """Raise :class:`CycleDetectedError` if ``dag`` has any cycle."""
    cycles = dag.detect_cycles()
    if cycles:
        raise CycleDetectedError(cycles[0])


def assert_dependency_satisfied(dag: DAG, task_id: str, completed: set[str]) -> None:
    """Raise :class:`DependencyError` if any parent of ``task_id`` is not in ``completed``.

    Skips parents that are not in the graph (defensive).
    """
    for parent in dag.parents(task_id):
        if parent not in completed:
            raise DependencyError(task_id, parent, reason=f"parent '{parent}' not completed")


__all__ = [
    "DAG",
    "assert_dependency_satisfied",
    "validate_no_cycles",
]
