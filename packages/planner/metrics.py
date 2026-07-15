"""Planner metrics calculator.

Sprint 3.1 — Intelligent Task Planning Engine.

Computes structural metrics about a :class:`TaskPlan` such as step counts,
dependency depth, and an approximate complexity score.  These metrics are
purely derived from the plan's graph structure — no runtime data is required.
"""

from __future__ import annotations

from collections import deque

from .interfaces import IMetrics
from .models import PlanMetrics, PlanStep, TaskPlan


class PlannerMetrics(IMetrics):
    """Concrete implementation of :class:`IMetrics`.

    Metrics computed:

    * **planning_time** — passed through (wall-clock seconds).
    * **total_steps** — ``len(plan.steps)``.
    * **estimated_total_duration** — sum of all step durations.
    * **sequential_steps** — steps that have at least one dependency.
    * **parallel_steps** — steps with zero dependencies (root nodes).
    * *bonus* — :meth:`depth`, :meth:`dependency_count`, :meth:`complexity`.
    """

    # -- IMetrics ------------------------------------------------------------

    def compute(self, plan: TaskPlan, planning_time: float = 0.0) -> PlanMetrics:
        """Compute structural metrics for *plan*.

        Args:
            plan: The plan to analyze.
            planning_time: Wall-clock seconds spent building the plan.

        Returns:
            A populated :class:`PlanMetrics` instance.
        """
        total = len(plan.steps)
        estimated = sum(s.estimated_duration for s in plan.steps)
        sequential = sum(1 for s in plan.steps if s.dependencies)
        parallel = total - sequential

        return PlanMetrics(
            planning_time=planning_time,
            estimated_total_duration=estimated,
            total_steps=total,
            sequential_steps=sequential,
            parallel_steps=parallel,
        )

    # -- additional analysis methods -----------------------------------------

    def depth(self, plan: TaskPlan) -> int:
        """Return the longest dependency chain length (graph depth).

        A plan with no dependencies has depth ``0``.  A linear chain of
        three steps has depth ``2`` (edge count of the longest path).
        """
        if not plan.steps:
            return 0

        step_ids = {s.id for s in plan.steps}
        adj: dict[str, list[str]] = {s.id: list(s.dependencies) for s in plan.steps}
        memo: dict[str, int] = {}

        def _longest_path(node: str) -> int:
            if node in memo:
                return memo[node]
            deps = [d for d in adj.get(node, []) if d in step_ids]
            if not deps:
                memo[node] = 0
                return 0
            result = 1 + max(_longest_path(d) for d in deps)
            memo[node] = result
            return result

        return max(_longest_path(s.id) for s in plan.steps)

    def dependency_count(self, plan: TaskPlan) -> int:
        """Return the total number of dependency edges in the plan."""
        return sum(len(s.dependencies) for s in plan.steps)

    def complexity(self, plan: TaskPlan) -> float:
        """Return an approximate complexity score (0.0 – 1.0+).

        Combines step count, dependency density, and graph depth into a
        single heuristic value useful for comparing plans.
        """
        n = len(plan.steps)
        if n == 0:
            return 0.0

        max_possible_edges = n * (n - 1)  # directed, no self-loops
        actual_edges = self.dependency_count(plan)
        density = actual_edges / max_possible_edges if max_possible_edges > 0 else 0.0
        d = self.depth(plan)
        max_depth = n - 1 if n > 1 else 1
        depth_ratio = d / max_depth if max_depth > 0 else 0.0

        # Weighted combination: density (0.4) + depth ratio (0.3) + log steps (0.3)
        import math

        step_factor = min(math.log2(n + 1) / math.log2(21), 1.0)  # normalise to ~20 steps
        return round(0.4 * density + 0.3 * depth_ratio + 0.3 * step_factor, 4)

    def critical_path(self, plan: TaskPlan) -> list[str]:
        """Return step IDs along the longest-duration dependency path.

        Uses a topological traversal.  For each step the cumulative duration
        is the step's own duration plus the max cumulative duration of its
        dependencies.  The path is reconstructed by backtracking from the
        step with the highest cumulative duration.
        """
        if not plan.steps:
            return []

        step_map: dict[str, PlanStep] = {s.id: s for s in plan.steps}
        in_degree: dict[str, int] = {s.id: 0 for s in plan.steps}
        for s in plan.steps:
            for dep in s.dependencies:
                if dep in in_degree:
                    in_degree[s.id] += 1

        # Topological order (Kahn)
        queue: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        topo: list[str] = []
        while queue:
            node = queue.popleft()
            topo.append(node)
            for s in plan.steps:
                if node in s.dependencies:
                    in_degree[s.id] -= 1
                    if in_degree[s.id] == 0:
                        queue.append(s.id)

        # Forward pass: cumulative duration
        cum: dict[str, float] = {s.id: s.estimated_duration for s in plan.steps}
        for node in topo:
            for s in plan.steps:
                if node in s.dependencies:
                    candidate = cum[node] + s.estimated_duration
                    if candidate > cum.get(s.id, 0.0):
                        cum[s.id] = candidate

        if not cum:
            return []

        # Find the end node with the max cumulative duration
        end_node = max(cum, key=cum.get)  # type: ignore[arg-type]

        # Backtrack
        path: list[str] = [end_node]
        current = end_node
        while True:
            deps = step_map[current].dependencies
            if not deps:
                break
            # Pick the dependency with the highest cumulative duration
            best = max(deps, key=lambda d: cum.get(d, 0.0))
            path.append(best)
            current = best
            if len(path) > len(plan.steps) + 1:
                break  # safety guard

        path.reverse()
        return path

    def ready_steps(self, plan: TaskPlan, completed: set[str] | None = None) -> list[str]:
        """Return IDs of steps whose dependencies are all in *completed*.

        Args:
            plan: The plan to inspect.
            completed: Set of step IDs already completed. Defaults to empty.
        """
        completed = completed or set()
        ready: list[str] = []
        for step in plan.steps:
            if all(dep in completed for dep in step.dependencies):
                ready.append(step.id)
        return ready
