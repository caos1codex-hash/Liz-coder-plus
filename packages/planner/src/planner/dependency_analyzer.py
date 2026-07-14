"""DependencyAnalyzer — infers dependencies between tasks (Sprint 2.8).

Given a list of :class:`~planner.task.Task` objects (typically the
output of :class:`~planner.task_decomposer.TaskDecomposer`), the
:class:`DependencyAnalyzer` produces a ``{task_id: [dep_id, ...]}``
mapping that captures which tasks must complete before each task can
start.

Strategies (combined):

1. **Explicit hints** — tasks may declare ``metadata['depends_on']``
   listing task ids (or titles) they depend on. These are honoured
   verbatim after id resolution.

2. **Capability ordering** — certain capability pairs imply an order,
   e.g. ``plan`` should run before ``code.generate``, which should
   run before ``code.review``. The analyzer applies a configurable
   ordering table.

3. **Phase tags** — tasks may declare ``metadata['phase']`` (an int
   or string). Tasks in earlier phases run first; within a phase,
   tasks are independent.

4. **Sequential fallback** — if no other signal is present, tasks
   are linked in declaration order so that the plan is at least
   sequentially executable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .task import Task

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class DependencyAnalyzerConfig:
    """Configuration for :class:`DependencyAnalyzer`.

    Attributes:
        capability_order:  Mapping ``{capability: phase_int}``.
                           Tasks whose required_capabilities include
                           ``cap`` are assigned the minimum phase
                           they appear in.
        strict_phase_ordering: If True, tasks in different phases are
                           always linked (earlier -> later). If False,
                           phase is only a tiebreaker.
        sequential_fallback:   If True (default), tasks with no other
                           dependency signal are linked in declaration
                           order.
    """

    capability_order: dict[str, int] = field(
        default_factory=lambda: {
            "plan": 0,
            "decompose": 0,
            "planning": 0,
            "research": 1,
            "web.search": 1,
            "documentation.lookup": 1,
            "code.generate": 2,
            "code.refactor": 2,
            "code.fix": 2,
            "filesystem.write": 3,
            "filesystem.read": 3,
            "code.test": 4,
            "code.review": 5,
            "audit": 5,
            "git.commit": 6,
            "git.push": 7,
            "terminal.execute": 8,
            "memory.store": 9,
        }
    )
    strict_phase_ordering: bool = False
    sequential_fallback: bool = True


# ----------------------------------------------------------------------
# DependencyAnalyzer
# ----------------------------------------------------------------------


class DependencyAnalyzer:
    """Infers dependencies between tasks.

    Usage::

        analyzer = DependencyAnalyzer()
        deps = analyzer.analyze(tasks)
        # deps: {task_id: [dep_id, ...]}
        for task in tasks:
            task.dependencies = deps.get(task.id, [])
    """

    def __init__(self, *, config: DependencyAnalyzerConfig | None = None) -> None:
        self._config = config or DependencyAnalyzerConfig()

    @property
    def config(self) -> DependencyAnalyzerConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, tasks: list[Task]) -> dict[str, list[str]]:
        """Return ``{task_id: [dep_id, ...]}`` for ``tasks``.

        Never raises for unknown references — broken edges are simply
        dropped (and will be flagged by :meth:`ExecutionPlan.validate`).
        """
        if not tasks:
            return {}
        # Index tasks by id and by lowercased title for hint resolution.
        by_id = {t.id: t for t in tasks}
        by_title: dict[str, Task] = {}
        for t in tasks:
            if t.title:
                by_title.setdefault(t.title.lower(), t)

        # Phase per task.
        phases = self._compute_phases(tasks)

        deps: dict[str, set[str]] = {t.id: set() for t in tasks}

        # 1. Explicit hints.
        for t in tasks:
            hints = t.metadata.get("depends_on", [])
            if isinstance(hints, str):
                hints = [hints]
            for hint in hints:
                resolved = self._resolve_hint(hint, by_id, by_title)
                if resolved and resolved != t.id and resolved in deps:
                    deps[t.id].add(resolved)

        # 2. Capability ordering.
        if self._config.capability_order:
            self._apply_capability_order(tasks, phases, deps)

        # 3. Phase ordering (strict mode).
        if self._config.strict_phase_ordering:
            self._apply_phase_order(tasks, phases, deps)

        # 4. Sequential fallback.
        if self._config.sequential_fallback:
            self._apply_sequential_fallback(tasks, deps)

        # Convert sets to sorted lists for determinism.
        return {tid: sorted(d) for tid, d in deps.items()}

    def apply(self, tasks: list[Task]) -> None:
        """Analyze and write dependencies back onto each task in place."""
        deps = self.analyze(tasks)
        for t in tasks:
            t.dependencies = list(deps.get(t.id, []))

    # ------------------------------------------------------------------
    # Phase computation
    # ------------------------------------------------------------------

    def _compute_phases(self, tasks: list[Task]) -> dict[str, int]:
        """Assign a phase integer to each task.

        - Explicit ``metadata['phase']`` (int or numeric str) wins.
        - Otherwise, the minimum phase across the task's capabilities.
        - Otherwise, a large number (so it sorts last).
        """
        phases: dict[str, int] = {}
        for t in tasks:
            explicit = t.metadata.get("phase")
            if explicit is not None:
                try:
                    phases[t.id] = int(explicit)
                    continue
                except (TypeError, ValueError):
                    pass
            cap_phases = [
                self._config.capability_order[c]
                for c in t.required_capabilities
                if c in self._config.capability_order
            ]
            if cap_phases:
                phases[t.id] = min(cap_phases)
            else:
                phases[t.id] = 9999
        return phases

    # ------------------------------------------------------------------
    # Hint resolution
    # ------------------------------------------------------------------

    def _resolve_hint(
        self,
        hint: str,
        by_id: dict[str, Task],
        by_title: dict[str, Task],
    ) -> str | None:
        """Resolve a hint (id or title) to a task id."""
        if hint in by_id:
            return hint
        # Try case-insensitive title match.
        lower = hint.lower()
        if lower in by_title:
            return by_title[lower].id
        return None

    # ------------------------------------------------------------------
    # Capability ordering
    # ------------------------------------------------------------------

    def _apply_capability_order(
        self,
        tasks: list[Task],
        phases: dict[str, int],
        deps: dict[str, set[str]],
    ) -> None:
        """Link tasks based on capability phases.

        For each task A with phase p_A, find every task B with phase
        p_B < p_A and add B as a dependency of A — but only if A and B
        don't already have a path between them. To avoid over-linking,
        we only add edges between *adjacent* phases (p_A = p_B + 1).
        """
        # Group tasks by phase.
        by_phase: dict[int, list[Task]] = {}
        for t in tasks:
            by_phase.setdefault(phases[t.id], []).append(t)
        sorted_phases = sorted(by_phase.keys())
        # For each pair of adjacent phases, link every task in the
        # later phase to *every* task in the earlier phase. This is
        # aggressive but correct: e.g. all "code.generate" tasks depend
        # on all "plan" tasks.
        for i in range(1, len(sorted_phases)):
            earlier = by_phase[sorted_phases[i - 1]]
            later = by_phase[sorted_phases[i]]
            for lt in later:
                for et in earlier:
                    if et.id != lt.id:
                        deps[lt.id].add(et.id)

    # ------------------------------------------------------------------
    # Phase ordering (strict)
    # ------------------------------------------------------------------

    def _apply_phase_order(
        self,
        tasks: list[Task],
        phases: dict[str, int],
        deps: dict[str, set[str]],
    ) -> None:
        """In strict mode, link tasks across ALL earlier phases (not just adjacent)."""
        sorted_tasks = sorted(tasks, key=lambda t: phases[t.id])
        for i, later in enumerate(sorted_tasks):
            for earlier in sorted_tasks[:i]:
                if phases[earlier.id] < phases[later.id]:
                    deps[later.id].add(earlier.id)

    # ------------------------------------------------------------------
    # Sequential fallback
    # ------------------------------------------------------------------

    def _apply_sequential_fallback(
        self,
        tasks: list[Task],
        deps: dict[str, set[str]],
    ) -> None:
        """If a task has no deps after the other passes, link it to the previous task.

        This guarantees the plan is at least sequentially executable
        even when no other signal is present.
        """
        previous: Task | None = None
        for t in tasks:
            if not deps[t.id] and previous is not None:
                deps[t.id].add(previous.id)
            previous = t


__all__ = ["DependencyAnalyzer", "DependencyAnalyzerConfig"]
