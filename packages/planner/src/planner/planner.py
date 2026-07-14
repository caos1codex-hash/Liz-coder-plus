"""TaskPlanner — the top-level planner that ties everything together (Sprint 2.8).

The :class:`TaskPlanner` is the single entry point users interact with.
It composes the lower-level components:

- :class:`~planner.task_decomposer.TaskDecomposer`
- :class:`~planner.dependency_analyzer.DependencyAnalyzer`
- :class:`~planner.estimator.CostEstimator`
- :class:`~planner.capability_matcher.CapabilityMatcher`

and exposes the high-level operations required by Sprint 2.8:

- :meth:`create_plan`    — objective -> ExecutionPlan.
- :meth:`update_plan`    — apply incremental changes.
- :meth:`replan`         — re-decompose / re-assign after failures.
- :meth:`split_task`     — split a task into smaller subtasks.
- :meth:`merge_tasks`    — merge multiple tasks into one.
- :meth:`estimate`       — re-estimate a task or plan.
- :meth:`validate`       — structural validation.
- :meth:`assign_agents`  — (re)assign agents to all tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .capability_matcher import (
    AgentRegistryAdapter,
    CapabilityMatcher,
    MatcherWeights,
)
from .dependency_analyzer import DependencyAnalyzer, DependencyAnalyzerConfig
from .estimator import CostEstimator, EstimatorConfig
from .exceptions import (
    AgentAssignmentError,
    PlanValidationError,
    TaskNotFoundError,
)
from .execution_plan import ExecutionPlan
from .models import TaskStatus
from .task import Task
from .task_decomposer import DecomposerConfig, TaskDecomposer
from .utils import merge_dicts, new_task_id, now_iso

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class PlannerConfig:
    """Top-level configuration for :class:`TaskPlanner`.

    Attributes:
        decomposer:  TaskDecomposer config.
        analyzer:    DependencyAnalyzer config.
        estimator:   CostEstimator config.
        matcher:     CapabilityMatcher weights.
        auto_estimate: If True, :meth:`create_plan` estimates every task.
        auto_assign:   If True, :meth:`create_plan` assigns agents.
        auto_validate: If True, :meth:`create_plan` validates the plan
                       and raises if invalid.
    """

    decomposer: DecomposerConfig = field(default_factory=DecomposerConfig)
    analyzer: DependencyAnalyzerConfig = field(default_factory=DependencyAnalyzerConfig)
    estimator: EstimatorConfig = field(default_factory=EstimatorConfig)
    matcher: MatcherWeights = field(default_factory=MatcherWeights)
    auto_estimate: bool = True
    auto_assign: bool = True
    auto_validate: bool = True


# ----------------------------------------------------------------------
# TaskPlanner
# ----------------------------------------------------------------------


class TaskPlanner:
    """Top-level planner.

    Usage::

        planner = TaskPlanner()
        plan = planner.create_plan("Create an AI code editor")
        # plan.tasks, plan.graph, plan.execution_order
    """

    def __init__(
        self,
        *,
        config: PlannerConfig | None = None,
        decomposer: TaskDecomposer | None = None,
        analyzer: DependencyAnalyzer | None = None,
        estimator: CostEstimator | None = None,
        matcher: CapabilityMatcher | None = None,
        registry_adapter: AgentRegistryAdapter | None = None,
        semantic_memory: Any | None = None,
    ) -> None:
        self._config = config or PlannerConfig()
        self._decomposer = decomposer or TaskDecomposer(config=self._config.decomposer)
        self._analyzer = analyzer or DependencyAnalyzer(config=self._config.analyzer)
        self._estimator = estimator or CostEstimator(config=self._config.estimator)
        self._matcher = matcher or CapabilityMatcher(
            adapter=registry_adapter,
            weights=self._config.matcher,
        )
        self._registry_adapter = registry_adapter or self._matcher.adapter
        self._semantic_memory = semantic_memory
        # Plan history for observability.
        self._plan_history: list[ExecutionPlan] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> PlannerConfig:
        return self._config

    @property
    def decomposer(self) -> TaskDecomposer:
        return self._decomposer

    @property
    def analyzer(self) -> DependencyAnalyzer:
        return self._analyzer

    @property
    def estimator(self) -> CostEstimator:
        return self._estimator

    @property
    def matcher(self) -> CapabilityMatcher:
        return self._matcher

    @property
    def registry_adapter(self) -> AgentRegistryAdapter:
        return self._registry_adapter

    # ------------------------------------------------------------------
    # create_plan
    # ------------------------------------------------------------------

    def create_plan(
        self,
        objective: str,
        *,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionPlan:
        """Decompose ``objective`` into a full :class:`ExecutionPlan`.

        Steps:

        1. (Optional) Query semantic memory for similar past plans.
        2. Decompose the objective into subtasks.
        3. Analyze / infer dependencies between subtasks.
        4. (Optional) Estimate cost / time / tokens for each task.
        5. (Optional) Assign agents to each task.
        6. (Optional) Validate the plan.

        The plan is registered in ``self._plan_history`` for debugging.
        """
        ctx = dict(context or {})
        # 1. Semantic memory lookup (best-effort).
        similar: list[dict[str, Any]] = []
        if self._semantic_memory is not None:
            try:
                similar = self._semantic_memory.retrieve_similar(objective, top_k=3)
                ctx["similar_plans"] = similar
            except Exception:  # noqa: BLE001
                logger.exception("semantic memory lookup failed")

        # 2. Decompose.
        subtasks = self._decomposer.decompose(objective, context=ctx)
        # 3. Analyze dependencies.
        self._analyzer.apply(subtasks)

        # 4. Build the plan.
        plan = ExecutionPlan(
            id=new_task_id("plan"),
            objective=objective,
            metadata=merge_dicts(
                {
                    "source": "task_planner",
                    "created_by": "TaskPlanner.create_plan",
                    "similar_plans_count": len(similar),
                },
                metadata or {},
            ),
        )
        for task in subtasks:
            plan.add_task(task)

        # 5. Estimate.
        if self._config.auto_estimate:
            self.estimate(plan)

        # 6. Assign agents.
        if self._config.auto_assign:
            self.assign_agents(plan)

        # 7. Validate.
        if self._config.auto_validate:
            errors = plan.validate()
            if errors:
                raise PlanValidationError(errors, plan_id=plan.id)

        self._plan_history.append(plan)
        return plan

    # ------------------------------------------------------------------
    # update_plan
    # ------------------------------------------------------------------

    def update_plan(
        self,
        plan: ExecutionPlan,
        *,
        add_tasks: list[Task] | None = None,
        remove_tasks: list[str] | None = None,
        reassign: bool = False,
        reestimate: bool = False,
    ) -> ExecutionPlan:
        """Apply incremental updates to ``plan`` in place.

        Args:
            add_tasks:    Tasks to add.
            remove_tasks: Task ids to remove.
            reassign:     If True, re-run :meth:`assign_agents` on
                          tasks without an ``assigned_agent``.
            reestimate:   If True, re-run :meth:`estimate` on all tasks.
        """
        for t in add_tasks or []:
            plan.add_task(t)
        for tid in remove_tasks or []:
            plan.remove_task(tid)
        if reestimate:
            self.estimate(plan)
        if reassign:
            self.assign_agents(plan, only_unassigned=True)
        return plan

    # ------------------------------------------------------------------
    # replan
    # ------------------------------------------------------------------

    def replan(
        self,
        plan: ExecutionPlan,
        *,
        failed_task_id: str | None = None,
        error: str = "",
    ) -> ExecutionPlan:
        """Re-decompose and re-assign a plan after a failure.

        Strategy:

        1. If ``failed_task_id`` is provided and the task is FAILED,
           split it into smaller subtasks (via :meth:`split_task`).
        2. Re-infer dependencies (to incorporate the new subtasks).
        3. Re-estimate.
        4. Re-assign agents.
        5. Validate.
        """
        if failed_task_id is not None:
            failed_task = plan.tasks.get(failed_task_id)
            if failed_task is None:
                raise TaskNotFoundError(failed_task_id)
            if failed_task.status == TaskStatus.FAILED:
                if failed_task.can_retry:
                    # Reset for retry (don't split).
                    failed_task.reset()
                else:
                    # Terminal failure: split into smaller subtasks.
                    self.split_task(plan, failed_task_id, factor=2)
            else:
                # Reset the task so it can be retried.
                failed_task.reset()
        # Re-analyze dependencies from scratch.
        all_tasks = list(plan.tasks.values())
        # Clear existing deps.
        for t in all_tasks:
            t.dependencies = []
        # Rebuild the graph.
        from .graph import DAG

        plan.graph = DAG()
        for t in all_tasks:
            plan.add_task(t) if t.id not in plan.tasks else None
        # Re-apply analyzer.
        self._analyzer.apply(all_tasks)
        # Re-add edges to the graph.
        for t in all_tasks:
            for dep in t.dependencies:
                if dep in plan.tasks:
                    try:
                        plan.graph.add_dependency(t.id, dep)
                    except Exception:  # noqa: BLE001
                        pass
        # Re-estimate and re-assign.
        self.estimate(plan)
        self.assign_agents(plan)
        # Validate.
        if self._config.auto_validate:
            errors = plan.validate()
            if errors:
                raise PlanValidationError(errors, plan_id=plan.id)
        self._plan_history.append(plan)
        return plan

    # ------------------------------------------------------------------
    # split_task
    # ------------------------------------------------------------------

    def split_task(
        self,
        plan: ExecutionPlan,
        task_id: str,
        *,
        factor: int = 2,
    ) -> list[Task]:
        """Split ``task_id`` into ``factor`` smaller subtasks.

        The original task is removed and the new subtasks are added.
        Each subtask inherits the original's capabilities, priority and
        description (suffixed with a part indicator). The new subtasks
        depend on each other sequentially to preserve the original
        semantics.

        Returns the list of new subtasks.
        """
        if factor < 2:
            raise ValueError("factor must be >= 2")
        original = plan.require_task(task_id)
        # Create the new subtasks.
        new_tasks: list[Task] = []
        for i in range(factor):
            t = Task(
                id=new_task_id("task"),
                title=f"{original.title} (part {i + 1}/{factor})",
                description=(f"{original.description} [split part {i + 1} of {factor}]"),
                required_capabilities=list(original.required_capabilities),
                priority=original.priority,
                metadata=merge_dicts(
                    original.metadata,
                    {"split_from": original.id, "split_part": i + 1},
                ),
            )
            new_tasks.append(t)
        # Sequential deps: part 2 depends on part 1, etc.
        for i in range(1, len(new_tasks)):
            new_tasks[i].dependencies.append(new_tasks[i - 1].id)
        # Inherit the original's dependents (tasks that depended on it).
        dependents = [t for t in plan.tasks.values() if original.id in t.dependencies]
        # Remove the original.
        plan.remove_task(original.id)
        # Add the new subtasks.
        for t in new_tasks:
            plan.add_task(t)
        # Rewire dependents to depend on the LAST new subtask.
        for dep in dependents:
            dep.dependencies = [d for d in dep.dependencies if d != original.id]
            dep.dependencies.append(new_tasks[-1].id)
            # Update the graph too.
            try:
                plan.graph.add_dependency(dep.id, new_tasks[-1].id)
            except Exception:  # noqa: BLE001
                pass
        # Estimate the new subtasks.
        for t in new_tasks:
            self._estimator.estimate(t).apply_to(t)
        return new_tasks

    # ------------------------------------------------------------------
    # merge_tasks
    # ------------------------------------------------------------------

    def merge_tasks(
        self,
        plan: ExecutionPlan,
        task_ids: list[str],
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> Task:
        """Merge multiple tasks into a single new task.

        The merged task inherits the union of capabilities and the
        maximum priority. Its dependencies are the union of the input
        tasks' dependencies (minus the input tasks themselves). All
        input tasks are removed.

        Returns the new merged task.
        """
        if len(task_ids) < 2:
            raise ValueError("merge_tasks requires at least 2 task ids")
        tasks_to_merge = [plan.require_task(tid) for tid in task_ids]
        # Union of capabilities.
        caps: list[str] = []
        for t in tasks_to_merge:
            for c in t.required_capabilities:
                if c not in caps:
                    caps.append(c)
        # Max priority (by weight, not by string comparison).
        from .models import priority_weight

        priority = max(tasks_to_merge, key=lambda t: priority_weight(t.priority)).priority
        # Union of dependencies (excluding the tasks being merged).
        deps: set[str] = set()
        for t in tasks_to_merge:
            for d in t.dependencies:
                if d not in task_ids:
                    deps.add(d)
        # Build the merged task.
        merged_title = title or " + ".join(t.title for t in tasks_to_merge)
        merged_desc = description or "\n".join(
            t.description for t in tasks_to_merge if t.description
        )
        merged = Task(
            id=new_task_id("task"),
            title=merged_title,
            description=merged_desc,
            required_capabilities=caps,
            priority=priority,
            dependencies=sorted(deps),
            metadata={
                "merged_from": list(task_ids),
                "merged_at": now_iso(),
            },
        )
        # Remove the originals.
        for tid in task_ids:
            plan.remove_task(tid)
        # Add the merged task.
        plan.add_task(merged)
        # Estimate the merged task.
        self._estimator.estimate(merged).apply_to(merged)
        return merged

    # ------------------------------------------------------------------
    # estimate
    # ------------------------------------------------------------------

    def estimate(self, plan_or_task: ExecutionPlan | Task) -> dict[str, Any]:
        """Estimate a plan or a single task.

        Returns a dict with the estimate summary.
        """
        if isinstance(plan_or_task, Task):
            est = self._estimator.estimate(plan_or_task)
            est.apply_to(plan_or_task)
            return est.to_dict()
        # Plan: estimate every non-terminal task.
        plan = plan_or_task
        for t in plan.tasks.values():
            est = self._estimator.estimate(t)
            est.apply_to(t)
        stats = plan.statistics
        return stats.to_dict()

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(self, plan: ExecutionPlan) -> list[str]:
        """Return a list of validation errors for ``plan``."""
        return plan.validate()

    # ------------------------------------------------------------------
    # assign_agents
    # ------------------------------------------------------------------

    def assign_agents(
        self,
        plan: ExecutionPlan,
        *,
        only_unassigned: bool = False,
    ) -> dict[str, str]:
        """Assign agents to every task in ``plan``.

        Args:
            only_unassigned: If True, only assign tasks without an
                              ``assigned_agent``; leave existing
                              assignments untouched.

        Returns ``{task_id: agent_id}`` for every assigned task.
        """
        result: dict[str, str] = {}
        for t in plan.tasks.values():
            if only_unassigned and t.assigned_agent is not None:
                result[t.id] = t.assigned_agent
                continue
            if TaskStatus.is_terminal(t.status):
                # Don't re-assign completed/cancelled tasks.
                if t.assigned_agent:
                    result[t.id] = t.assigned_agent
                continue
            try:
                agent_id = self._matcher.assign(t)
                result[t.id] = agent_id
            except AgentAssignmentError:
                # Leave unassigned; validate() can flag if needed.
                continue
        return result

    # ------------------------------------------------------------------
    # History / observability
    # ------------------------------------------------------------------

    def history(self) -> list[ExecutionPlan]:
        """Return a copy of the plan history (newest last)."""
        return list(self._plan_history)

    def metrics(self) -> dict[str, Any]:
        """Return aggregate metrics about planner activity."""
        return {
            "plans_created": len(self._plan_history),
            "estimator": self._estimator.metrics(),
            "matcher": self._matcher.metrics(),
            "templates": len(self._decomposer.templates),
            "agents_tracked": len(self._registry_adapter.list_agents()),
        }

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def add_agent(self, agent_info: Any) -> None:
        """Register an agent with the matcher's adapter.

        ``agent_info`` can be an :class:`~planner.capability_matcher.AgentInfo`
        or a dict that ``AgentInfo.from_dict`` can parse.
        """
        from .capability_matcher import AgentInfo

        if isinstance(agent_info, dict):
            agent_info = AgentInfo.from_dict(agent_info)
        self._registry_adapter.register(agent_info)

    def remove_agent(self, agent_id: str) -> bool:
        """Unregister an agent from the matcher's adapter."""
        return self._registry_adapter.unregister(agent_id)

    def __repr__(self) -> str:
        return (
            f"TaskPlanner(plans={len(self._plan_history)}, "
            f"agents={len(self._registry_adapter.list_agents())})"
        )


__all__ = ["PlannerConfig", "TaskPlanner"]
