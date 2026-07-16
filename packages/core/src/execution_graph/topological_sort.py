"""Topological Sort using Kahn's Algorithm.

Sprint 3.8.

Provides Kahn's algorithm for computing a topological ordering of a
DependencyGraph. Raises DependencyCycleError if a cycle is detected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .dependency_graph import DependencyCycleError

if TYPE_CHECKING:
    from .dependency_graph import DependencyGraph

logger = logging.getLogger(__name__)


def kahn_sort(graph: DependencyGraph) -> list[str]:
    """Compute a topological ordering using Kahn's algorithm.

    Ties are broken by priority (lower = higher priority) and then
    alphabetically by node name.

    Args:
        graph: The DependencyGraph to sort.

    Returns:
        List of node names in topological order.

    Raises:
        DependencyCycleError: If a cycle is detected (sorted list
            is shorter than the total number of nodes).
    """
    # Compute in-degree for each node.
    in_degree: dict[str, int] = {}
    for node in graph.nodes:
        in_degree[node] = len(graph.edges.get(node, set()))

    # Initialize ready queue with zero in-degree nodes.
    ready: list[str] = sorted(
        [n for n, deg in in_degree.items() if deg == 0],
        key=lambda n: (graph.priorities.get(n, 0), n),
    )

    result: list[str] = []

    while ready:
        node = ready.pop(0)
        result.append(node)

        # Decrement in-degree for dependents.
        for dependent in sorted(graph.dependents.get(node, set())):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                # Insert maintaining priority order.
                inserted = False
                dep_priority = graph.priorities.get(dependent, 0)
                for i, existing in enumerate(ready):
                    ex_priority = graph.priorities.get(existing, 0)
                    if dep_priority < ex_priority or (
                        dep_priority == ex_priority and dependent < existing
                    ):
                        ready.insert(i, dependent)
                        inserted = True
                        break
                if not inserted:
                    ready.append(dependent)

    # Check for cycles.
    if len(result) != len(graph.nodes):
        missing = graph.nodes - set(result)
        logger.warning(
            "Topological sort incomplete; %d nodes unsorted (possible cycle): %s",
            len(missing), sorted(missing),
        )
        raise DependencyCycleError(
            list(missing) if missing else ["unknown"]
        )

    logger.debug("Topological order: %s", result)
    return result


def topological_levels(graph: DependencyGraph) -> dict[str, int]:
    """Compute the topological level (depth) for each node.

    Level 0 = root nodes (no dependencies).
    Level N = max(parent levels) + 1.

    This is useful for determining which nodes can run in parallel:
    all nodes at the same level are independent of each other.

    Args:
        graph: The DependencyGraph.

    Returns:
        Dict mapping node name to its topological level.
    """
    order = kahn_sort(graph)
    levels: dict[str, int] = {}

    for node in order:
        parents = graph.edges.get(node, set())
        if not parents:
            levels[node] = 0
        else:
            parent_levels = [levels[p] for p in parents if p in levels]
            levels[node] = (max(parent_levels) + 1) if parent_levels else 0

    return levels


__all__ = ["kahn_sort", "topological_levels"]