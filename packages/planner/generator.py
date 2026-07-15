"""Plan generator.

Sprint 3.2 — Planner Engine Core.

Converts a user goal into a fully-constructed :class:`TaskPlan` by
orchestrating :class:`TaskAnalyzer`, :class:`PlanTemplate`, and
:class:`PlanValidator`.

The generator is the central piece of Sprint 3.2 — it ties together
analysis, template selection, step generation, dependency wiring,
and validation.
"""

from __future__ import annotations

from typing import Any

from .analyzer import TaskAnalyzer
from .enums import PlanStatus, Priority
from .exceptions import PlanValidationError
from .models import PlanStep, TaskPlan
from .templates import PlanTemplate, get_template
from .validator import PlanValidator


class PlanGenerator:
    """Generate a :class:`TaskPlan` from a natural-language goal.

    The generation pipeline is:

    1. **Analyse** the goal → :class:`PlanningContext`.
    2. **Select** a template based on ``context.task_type``.
    3. **Generate** steps from the template.
    4. **Wire** sequential dependencies (each step depends on the previous).
    5. **Apply** context metadata (agents, complexity).
    6. **Validate** the plan.

    Args:
        analyzer: Optional custom :class:`TaskAnalyzer`.
        validator: Optional custom :class:`PlanValidator`.
    """

    def __init__(
        self,
        analyzer: TaskAnalyzer | None = None,
        validator: PlanValidator | None = None,
    ) -> None:
        self._analyzer = analyzer or TaskAnalyzer()
        self._validator = validator or PlanValidator()

    # -- properties ----------------------------------------------------------

    @property
    def analyzer(self) -> TaskAnalyzer:
        return self._analyzer

    @property
    def validator(self) -> PlanValidator:
        return self._validator

    # -- core generation ------------------------------------------------------

    def generate(
        self,
        goal: str,
        description: str = "",
        priority: Any | None = None,
        template: PlanTemplate | None = None,
        **context_kwargs: Any,
    ) -> TaskPlan:
        """Generate a plan from a goal.

        Args:
            goal: The user-provided objective.
            description: Optional elaborated description.
            priority: Optional priority. Auto-detected from complexity if not set.
            template: Override the auto-selected template.
            **context_kwargs: Forwarded to :meth:`TaskAnalyzer.analyze`.

        Returns:
            A validated :class:`TaskPlan`.

        Raises:
            PlanValidationError: If the generated plan fails validation.
        """
        # 1. Analyse
        context = self._analyzer.analyze(goal, **context_kwargs)

        # 2. Select template
        tmpl = template or get_template(context.task_type)

        # 3. Generate steps
        steps = tmpl.generate_steps(context)

        # 4. Wire sequential dependencies (fill None placeholders)
        steps = self._wire_dependencies(steps)

        # 5. Determine priority from complexity if not set
        if priority is None:
            priority = self._complexity_to_priority(context.complexity)

        # 6. Build plan
        plan = TaskPlan(
            goal=goal,
            description=description or f"Plan generado para: {goal}",
            priority=Priority(priority),
            status=PlanStatus.PLANNING,
            steps=steps,
            metadata={
                "generated_by": "PlanGenerator",
                "task_type": context.task_type,
                "complexity": context.complexity,
                "template": tmpl.task_type,
                "analysis": context.to_dict(),
            },
        )

        # 7. Validate
        errors = self._validator.validate(plan)
        if errors:
            raise PlanValidationError(
                f"Generated plan has {len(errors)} validation error(s)",
                errors=errors,
            )

        return plan

    def generate_from_template(
        self,
        goal: str,
        template: PlanTemplate,
        description: str = "",
        priority: Any | None = None,
        **context_kwargs: Any,
    ) -> TaskPlan:
        """Generate a plan using a specific template.

        Convenience wrapper around :meth:`generate` with an explicit template.
        """
        return self.generate(
            goal=goal,
            description=description,
            priority=priority,
            template=template,
            **context_kwargs,
        )

    # -- step manipulation ----------------------------------------------------

    @staticmethod
    def add_step(plan: TaskPlan, step: PlanStep, after: str | None = None) -> TaskPlan:
        """Add a step to an existing plan.

        Args:
            plan: The plan to modify.
            step: The step to add.
            after: If set, insert *after* the step with this ID.
                Otherwise, append to the end.

        Returns:
            The modified plan (same instance, mutated in place).
        """
        if after is not None:
            idx = next(
                (i for i, s in enumerate(plan.steps) if s.id == after),
                len(plan.steps),
            )
            plan.steps.insert(idx + 1, step)
        else:
            plan.steps.append(step)

        # If no explicit dependencies, make it depend on the previous step
        if not step.dependencies and len(plan.steps) > 1:
            prev = plan.steps[-2] if step is plan.steps[-1] else plan.steps[-2]
            if prev.id != step.id:
                step.dependencies = [prev.id]

        plan.touch()
        return plan

    @staticmethod
    def remove_step(plan: TaskPlan, step_id: str) -> TaskPlan:
        """Remove a step from the plan and clean up dangling dependencies.

        Args:
            plan: The plan to modify.
            step_id: The ID of the step to remove.

        Returns:
            The modified plan.

        Raises:
            ValueError: If the step ID is not found.
        """
        idx = next((i for i, s in enumerate(plan.steps) if s.id == step_id), -1)
        if idx == -1:
            raise ValueError(f"Step '{step_id}' not found in plan")

        removed_id = plan.steps[idx].id
        plan.steps.pop(idx)

        # Remove dangling dependencies
        for step in plan.steps:
            step.dependencies = [d for d in step.dependencies if d != removed_id]

        plan.touch()
        return plan

    @staticmethod
    def optimize_order(plan: TaskPlan) -> TaskPlan:
        """Re-order steps so that dependencies come before dependents.

        Uses a simple topological sort (Kahn's algorithm).
        Steps without ordering constraints keep their relative order.
        """
        if not plan.steps:
            return plan

        step_map = {s.id: s for s in plan.steps}
        in_degree: dict[str, int] = {s.id: 0 for s in plan.steps}
        for s in plan.steps:
            for dep in s.dependencies:
                if dep in in_degree:
                    in_degree[s.id] += 1

        # Seed with zero-indegree steps (preserve order)
        from collections import deque

        queue: deque[str] = deque()
        for s in plan.steps:
            if in_degree[s.id] == 0:
                queue.append(s.id)

        ordered: list[PlanStep] = []
        while queue:
            node = queue.popleft()
            ordered.append(step_map[node])
            for s in plan.steps:
                if node in s.dependencies:
                    in_degree[s.id] -= 1
                    if in_degree[s.id] == 0:
                        queue.append(s.id)

        plan.steps = ordered
        plan.touch()
        return plan

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _wire_dependencies(steps: list[PlanStep]) -> list[PlanStep]:
        """Replace ``None`` dependency placeholders with the previous step's ID.

        Each step that has ``None`` in its dependencies list gets the
        previous step's ID inserted instead.
        """
        for i, step in enumerate(steps):
            if None in step.dependencies:
                step.dependencies = [
                    steps[i - 1].id if d is None and i > 0 else d for d in step.dependencies
                ]
        return steps

    @staticmethod
    def _complexity_to_priority(complexity: str) -> str:
        """Map a complexity level to a :class:`Priority` value."""
        mapping = {
            "low": "low",
            "normal": "normal",
            "high": "high",
            "critical": "critical",
        }
        return mapping.get(complexity, "normal")
