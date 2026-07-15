"""Task analyzer.

Sprint 3.2 — Planner Engine Core.

Analyses a planning goal *before* plan generation.  Populates a
:class:`PlanningContext` with detected task type, complexity,
suggested agents, and keyword signals.

The analyzer orchestrates :class:`ComplexityEstimator` and
:class:`PlannerRuleEngine` but does **not** generate plans itself.
"""

from __future__ import annotations

from typing import Any

from .complexity import ComplexityEstimator
from .context import PlanningContext
from .rules import PlannerRuleEngine, create_default_rule_engine


class TaskAnalyzer:
    """Analyse a goal and enrich a :class:`PlanningContext`.

    Args:
        complexity_estimator: Optional custom estimator.
        rule_engine: Optional custom rule engine.
    """

    def __init__(
        self,
        complexity_estimator: ComplexityEstimator | None = None,
        rule_engine: PlannerRuleEngine | None = None,
    ) -> None:
        self._complexity = complexity_estimator or ComplexityEstimator()
        self._rules = rule_engine or create_default_rule_engine()

    # -- properties ----------------------------------------------------------

    @property
    def complexity_estimator(self) -> ComplexityEstimator:
        return self._complexity

    @property
    def rule_engine(self) -> PlannerRuleEngine:
        return self._rules

    # -- public API ----------------------------------------------------------

    def analyze(self, goal: str, **kwargs: Any) -> PlanningContext:
        """Build a fully-populated :class:`PlanningContext` from *goal*.

        The pipeline is:

        1. Create a bare context from *goal* + *kwargs*.
        2. Detect task type via :class:`ComplexityEstimator`.
        3. Estimate complexity.
        4. Detect keywords.
        5. Suggest step count.
        6. Apply rule engine (agent suggestions, type hints).

        Args:
            goal: The user-provided planning goal.
            **kwargs: Forwarded to :class:`PlanningContext` constructor
                (``user``, ``available_agents``, ``constraints``, etc.).

        Returns:
            A populated :class:`PlanningContext`.
        """
        ctx = PlanningContext(goal=goal, **kwargs)

        # Task type
        ctx.task_type = self._complexity.detect_task_type(goal)

        # Complexity
        level = self._complexity.estimate(goal)
        ctx.complexity = level.value

        # Keywords
        ctx.detected_keywords = self._complexity.detect_keywords(goal)

        # Step count
        ctx.estimated_steps = self._complexity.suggest_step_count(goal)

        # Rule engine
        self._rules.apply(ctx)

        # Merge suggested agents with available agents if set
        if ctx.available_agents:
            ctx.suggested_agents = [
                a for a in ctx.suggested_agents if a in ctx.available_agents
            ] + [a for a in ctx.available_agents if a not in ctx.suggested_agents]

        return ctx

    def analyze_quick(self, goal: str) -> dict[str, Any]:
        """Return a lightweight analysis dict (no full context object).

        Useful for debugging or preview.
        """
        ctx = self.analyze(goal)
        return {
            "task_type": ctx.task_type,
            "complexity": ctx.complexity,
            "estimated_steps": ctx.estimated_steps,
            "suggested_agents": ctx.suggested_agents,
            "detected_keywords": ctx.detected_keywords,
        }
