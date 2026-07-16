"""Dependency Graph for execution plans.

Sprint 3.8.

Builds and manages a Directed Acyclic Graph (DAG) from an ExecutablePlan
or a list of sub-task dicts. Provides operations for building the graph,
querying parent/child relationships, validating acyclicity, and computing
topological properties.

The graph uses task names as node identifiers (consistent with the
existing Planner's ``SubTask.depends_on`` string-based references).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class DependencyCycleError(Exception):
    """Raised when a cycle is detected in the dependency graph."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        formatted = " -> ".join(cycle)
        super().__init__(f"Dependency cycle detected: {formatted}")


@dataclass
class DependencyGraph:
    """Directed Acyclic Graph built from an execution plan.

    Uses task names as node identifiers. Edges point from a task to
    its dependencies (i.e., an edge A -> B means A depends on B, so
    B must complete before A can start).

    Attributes:
        nodes:      Set of all node names.
        edges:      Dict {node: set of nodes it depends on}.
        dependents: Dict {node: set of nodes that depend on it}.
        priorities: Dict {node: priority value} (lower = higher priority).
    """

    nodes: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    dependents: dict[str, set[str]] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_node(
        self,
        name: str,
        dependencies: Iterable[str] | None = None,
        *,
        priority: int = 0,
    ) -> None:
        """Add a node with optional dependencies.

        Args:
            name:         Node name (unique identifier).
            dependencies: Names of nodes this node depends on.
            priority:     Scheduling priority (lower = higher priority).
        """
        self.nodes.add(name)
        self.edges.setdefault(name, set())
        self.priorities[name] = priority

        deps = set(dependencies or [])
        self.edges[name] = deps
        for dep in deps:
            self.nodes.add(dep)
            self.edges.setdefault(dep, set())
            self.dependents.setdefault(dep, set()).add(name)
            self.priorities.setdefault(dep, 0)

    def remove_node(self, name: str) -> None:
        """Remove a node and all edges to/from it."""
        self.nodes.discard(name)
        self.edges.pop(name, None)
        self.priorities.pop(name, None)
        # Remove from dependents of its dependencies.
        for dep in self.edges.get(name, set()):
            self.dependents.get(dep, set()).discard(name)
        # Remove from edges of dependents.
        for dependent in self.dependents.get(name, set()):
            self.edges.get(dependent, set()).discard(name)
        self.dependents.pop(name, None)

    def add_dependency(self, node: str, depends_on: str) -> None:
        """Add a dependency edge: node depends on depends_on."""
        if node not in self.nodes:
            self.nodes.add(node)
            self.edges.setdefault(node, set())
            self.priorities.setdefault(node, 0)
        if depends_on not in self.nodes:
            self.nodes.add(depends_on)
            self.edges.setdefault(depends_on, set())
            self.priorities.setdefault(depends_on, 0)
        self.edges[node].add(depends_on)
        self.dependents.setdefault(depends_on, set()).add(node)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def parents(self, node: str) -> set[str]:
        """Return the nodes that *node* depends on (predecessors)."""
        return set(self.edges.get(node, set()))

    def children(self, node: str) -> set[str]:
        """Return the nodes that depend on *node* (successors)."""
        return set(self.dependents.get(node, set()))

    def roots(self) -> list[str]:
        """Return nodes with no dependencies (entry points)."""
        result = []
        for node in self.nodes:
            if not self.edges.get(node, set()):
                result.append(node)
        result.sort(key=lambda n: (self.priorities.get(n, 0), n))
        return result

    def leaves(self) -> list[str]:
        """Return nodes that nothing depends on (exit points)."""
        result = []
        for node in self.nodes:
            if not self.dependents.get(node, set()):
                result.append(node)
        result.sort(key=lambda n: (self.priorities.get(n, 0), n))
        return result

    def is_ready(self, node: str, completed: set[str]) -> bool:
        """Return True if all dependencies of *node* are completed."""
        deps = self.edges.get(node, set())
        return bool(deps) and all(d in completed for d in deps) if deps else not deps

    def remaining_dependencies(self, node: str, completed: set[str]) -> set[str]:
        """Return dependencies of *node* that are not yet completed."""
        deps = self.edges.get(node, set())
        return {d for d in deps if d not in completed}

    def in_degree(self, node: str) -> int:
        """Return the in-degree of a node (number of dependencies)."""
        return len(self.edges.get(node, set()))

    def out_degree(self, node: str) -> int:
        """Return the out-degree of a node (number of dependents)."""
        return len(self.dependents.get(node, set()))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the graph. Returns a list of error messages.

        Checks for:
        - Broken dependencies (dependencies on non-existent nodes).
        - Cycles.
        """
        errors: list[str] = []
        # Check broken deps.
        for node, deps in self.edges.items():
            for dep in deps:
                if dep not in self.nodes:
                    errors.append(
                        f"Node '{node}' depends on non-existent node '{dep}'"
                    )
        # Check cycles.
        try:
            self.detect_cycles()
        except DependencyCycleError as exc:
            errors.append(str(exc))
        return errors

    def detect_cycles(self) -> list[str] | None:
        """Detect cycles using DFS three-color algorithm.

        Returns the first cycle found as a list of node names,
        or None if no cycle exists.

        Raises:
            DependencyCycleError: If a cycle is detected.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self.nodes}
        path: list[str] = []

        def visit(node: str) -> list[str] | None:
            color[node] = GRAY
            path.append(node)
            for dep in self.edges.get(node, set()):
                if dep not in color:
                    continue  # broken dep, already reported in validate()
                if color[dep] == GRAY:
                    # Cycle found: extract from dep to current.
                    idx = path.index(dep)
                    return list(path[idx:]) + [dep]
                if color[dep] == WHITE:
                    result = visit(dep)
                    if result:
                        return result
            path.pop()
            color[node] = BLACK
            return None

        for node in sorted(self.nodes):
            if color[node] == WHITE:
                result = visit(node)
                if result:
                    raise DependencyCycleError(result)
        return None

    # ------------------------------------------------------------------
    # Topological order (delegates to topological_sort module)
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """Return a topological ordering of all nodes.

        Uses Kahn's algorithm with priority-based tie-breaking.

        Raises:
            DependencyCycleError: If a cycle is detected.
        """
        from .topological_sort import kahn_sort

        return kahn_sort(self)

    def topological_levels(self) -> dict[str, int]:
        """Return a dict mapping each node to its topological level.

        Level 0 = roots. Level N = max(parent levels) + 1.
        """
        levels: dict[str, int] = {}
        order = self.topological_order()

        for node in order:
            parents = self.edges.get(node, set())
            if not parents:
                levels[node] = 0
            else:
                parent_levels = [levels[p] for p in parents if p in levels]
                levels[node] = (max(parent_levels) + 1) if parent_levels else 0
        return levels

    # ------------------------------------------------------------------
    # Subgraph extraction
    # ------------------------------------------------------------------

    def subgraph(self, node_names: Iterable[str]) -> DependencyGraph:
        """Extract a subgraph containing only the specified nodes."""
        keep = set(node_names)
        new_graph = DependencyGraph()
        for node in keep:
            if node in self.nodes:
                deps = {d for d in self.edges.get(node, set()) if d in keep}
                new_graph.add_node(
                    node, dependencies=deps,
                    priority=self.priorities.get(node, 0),
                )
        return new_graph

    def affected_subgraph(self, failed_node: str) -> DependencyGraph:
        """Return the subgraph of nodes affected by a node failure.

        Includes the failed node and all its transitive dependents.
        """
        affected = {failed_node}
        stack = list(self.dependents.get(failed_node, set()))
        while stack:
            node = stack.pop()
            if node in affected:
                continue
            affected.add(node)
            stack.extend(self.dependents.get(node, set()))
        return self.subgraph(affected)

    # ------------------------------------------------------------------
    # Critical path
    # ------------------------------------------------------------------

    def critical_path(
        self,
        durations: dict[str, float] | None = None,
    ) -> list[str]:
        """Compute the critical path (longest path) through the DAG.

        Args:
            durations: Optional dict {node_name: duration_ms}.
                       If not provided, all nodes have duration 0.

        Returns:
            List of node names forming the critical path.
        """
        durations = durations or {}
        levels = self.topological_levels()
        order = self.topological_order()

        # Earliest completion time for each node.
        earliest: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}

        for node in order:
            node_dur = durations.get(node, 0.0)
            parents = self.edges.get(node, set())
            if not parents:
                earliest[node] = node_dur
                predecessor[node] = None
            else:
                best_parent = max(parents, key=lambda p: earliest.get(p, 0.0))
                earliest[node] = earliest.get(best_parent, 0.0) + node_dur
                predecessor[node] = best_parent

        if not order:
            return []

        # Find the node with the latest completion.
        end_node = max(order, key=lambda n: earliest.get(n, 0.0))

        # Trace back.
        path: list[str] = []
        current: str | None = end_node
        while current is not None:
            path.append(current)
            current = predecessor.get(current)
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": sorted(self.nodes),
            "edges": {n: sorted(d) for n, d in sorted(self.edges.items())},
            "dependents": {n: sorted(d) for n, d in sorted(self.dependents.items())},
            "priorities": dict(sorted(self.priorities.items())),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyGraph:
        graph = cls()
        for node in data.get("nodes", []):
            graph.nodes.add(node)
            graph.edges.setdefault(node, set())
            graph.priorities[node] = data.get("priorities", {}).get(node, 0)
        for node, deps in data.get("edges", {}).items():
            graph.edges[node] = set(deps)
            for dep in deps:
                graph.dependents.setdefault(dep, set()).add(node)
        return graph


__all__ = ["DependencyCycleError", "DependencyGraph"]