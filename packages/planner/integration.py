"""Agent-aware planner — connects the planner with the agent registry.

Sprint 3.3 — Agent-Aware Planning Integration.

:class:`AgentAwarePlanner` extends the Sprint 3.2 plan generation pipeline
with real agent awareness.  After generating a plan via the existing
:class:`PlanGenerator`, it:

1. Builds an :class:`AgentPlanningContext` from the agent registry.
2. Uses :class:`CapabilityMapper` to translate step needs to capabilities.
3. Uses :class:`AgentSelector` to find the best agents for each step.
4. Produces :class:`AgentAssignment` instances and attaches them to the plan.

The planner does **not** execute agents — it only enriches plans with
agent assignments for later consumption by an execution engine.

Typical usage::

    from multiagent.registry import AgentRegistry
    from planner.integration import AgentAwarePlanner

    registry = AgentRegistry()
    # ... register agents ...

    aw_planner = AgentAwarePlanner()
    plan = aw_planner.create_agent_aware_plan(
        goal="Crear API REST con autenticación",
        agent_registry=registry,
    )
    for step in plan.steps:
        assignment = step.metadata.get("agent_assignment")
        if assignment:
            print(f"{step.title} → {assignment['agent_id']}")
"""

from __future__ import annotations

import logging
from typing import Any

from .agent_context import AgentInfo, AgentPlanningContext
from .agent_selector import AgentSelector
from .assignment import AgentAssignment
from .capability_mapper import CapabilityMapper
from .exceptions import AgentNotFoundError, CapabilityMissingError
from .models import PlanStep, TaskPlan
from .planner import TaskPlanner

logger = logging.getLogger(__name__)


class AgentAwarePlanner:
    """Plan generation with real agent awareness.

    Wraps the existing :class:`TaskPlanner` and enriches generated plans
    with agent assignments based on the live agent registry.

    Args:
        planner: Optional :class:`TaskPlanner` instance. Defaults to a
            new :class:`TaskPlanner`.
        mapper: Optional :class:`CapabilityMapper`. Defaults to the
            standard mapper.
    """

    def __init__(
        self,
        planner: TaskPlanner | None = None,
        mapper: CapabilityMapper | None = None,
    ) -> None:
        self._planner = planner or TaskPlanner()
        self._mapper = mapper or CapabilityMapper.create_default()

    # -- properties ----------------------------------------------------------

    @property
    def planner(self) -> TaskPlanner:
        """The underlying :class:`TaskPlanner`."""
        return self._planner

    @property
    def mapper(self) -> CapabilityMapper:
        """The :class:`CapabilityMapper` for capability translation."""
        return self._mapper

    # -- public API ----------------------------------------------------------

    def create_agent_aware_plan(
        self,
        goal: str,
        agent_registry: Any,
        description: str = "",
        priority: Any | None = None,
        context: AgentPlanningContext | None = None,
        **kwargs: Any,
    ) -> TaskPlan:
        """Generate a plan with real agent assignments.

        This is the main entry point for Sprint 3.3.  It:

        1. Builds an :class:`AgentPlanningContext` from the registry.
        2. Generates a plan via :meth:`TaskPlanner.create_from_goal`.
        3. Assigns real agents to each step based on capabilities.
        4. Attaches :class:`AgentAssignment` metadata to steps and plan.

        Args:
            goal: Natural-language objective.
            agent_registry: An :class:`AgentRegistry` instance.
            description: Optional elaborated description.
            priority: Optional priority override.
            context: Optional pre-built :class:`AgentPlanningContext`.
                If not provided, one is built from *agent_registry*.
            **kwargs: Additional context kwargs forwarded to
                :meth:`TaskPlanner.create_from_goal`.

        Returns:
            A :class:`TaskPlan` with agent assignments in step metadata.

        Raises:
            CapabilityMissingError: If a required capability has no agent.
            AgentNotFoundError: If a suggested agent does not exist.
        """
        # 1. Build or use the agent context
        if context is not None:
            agent_ctx = context
        else:
            agent_ctx = AgentPlanningContext.from_registry(agent_registry)

        # Pass available agent names to the existing Sprint 3.2 pipeline.
        # Include both real names and lowercase equivalents so the Sprint 3.2
        # rule engine suggestions (e.g. "frontend_agent") survive the
        # analyzer's exact-match filter against available_agents.
        available_names = [a.name for a in agent_ctx.list_available()]
        lowercase_names = [
            n.lower().replace("agent", "_agent") for n in available_names
        ]
        # Deduplicate while preserving order
        seen: set[str] = set()
        combined: list[str] = []
        for name in available_names + lowercase_names:
            if name not in seen:
                seen.add(name)
                combined.append(name)
        kwargs["available_agents"] = combined

        # 2. Generate plan using existing Sprint 3.2 pipeline
        plan = self._planner.create_from_goal(
            goal=goal,
            description=description,
            priority=priority,
            **kwargs,
        )

        # 3. Assign agents to steps
        selector = AgentSelector(agent_ctx, self._mapper)
        assignments = self._assign_agents_to_plan(plan, agent_ctx, selector)

        # 4. Attach assignments to the plan
        plan.metadata["agent_assignments"] = [a.to_dict() for a in assignments]
        plan.metadata["agent_context_summary"] = {
            "total_agents": agent_ctx.agent_count,
            "available_agents": agent_ctx.available_count,
            "capabilities": agent_ctx.list_capabilities(),
        }
        plan.touch()

        return plan

    def assign_agents(
        self,
        plan: TaskPlan,
        agent_context: AgentPlanningContext,
    ) -> list[AgentAssignment]:
        """Assign agents to an existing plan.

        Useful for enriching a plan that was generated without agent
        awareness.

        Args:
            plan: The plan to enrich.
            agent_context: The agent planning context.

        Returns:
            List of :class:`AgentAssignment` instances.
        """
        selector = AgentSelector(agent_context, self._mapper)
        assignments = self._assign_agents_to_plan(plan, agent_context, selector)

        plan.metadata["agent_assignments"] = [a.to_dict() for a in assignments]
        plan.touch()

        return assignments

    def validate_agent_assignments(
        self,
        plan: TaskPlan,
        agent_context: AgentPlanningContext,
    ) -> list[str]:
        """Validate that all agent assignments in a plan are still valid.

        Checks that assigned agents exist and are available.

        Args:
            plan: The plan to validate.
            agent_context: The agent planning context.

        Returns:
            List of validation error messages (empty = valid).
        """
        errors: list[str] = []
        for step in plan.steps:
            assignment_data = step.metadata.get("agent_assignment")
            if not assignment_data:
                continue

            if isinstance(assignment_data, dict):
                assignment = AgentAssignment.from_dict(assignment_data)
            elif isinstance(assignment_data, AgentAssignment):
                assignment = assignment_data
            else:
                continue

            agent = agent_context.get_agent(assignment.agent_id)
            if agent is None:
                errors.append(
                    f"Step '{step.title}' ({step.id}): "
                    f"agent '{assignment.agent_id}' not found"
                )
                continue

            if not agent.available:
                errors.append(
                    f"Step '{step.title}' ({step.id}): "
                    f"agent '{agent.name}' is not available "
                    f"(status: {agent.status})"
                )

            if not agent.provides(assignment.capability):
                errors.append(
                    f"Step '{step.title}' ({step.id}): "
                    f"agent '{agent.name}' does not provide "
                    f"capability '{assignment.capability}'"
                )

        return errors

    # -- internals ------------------------------------------------------------

    def _assign_agents_to_plan(
        self,
        plan: TaskPlan,
        agent_ctx: AgentPlanningContext,
        selector: AgentSelector,
    ) -> list[AgentAssignment]:
        """Walk through plan steps and assign agents.

        For each step, the assignment strategy is:

        1. Use the step's ``required_agents`` (from Sprint 3.2 rule engine)
           to find the corresponding agent in the registry.
        2. If no direct match, resolve via the capability mapper.
        3. Fall back to the selector's general scoring.

        Args:
            plan: The generated plan.
            agent_ctx: The agent context.
            selector: The agent selector.

        Returns:
            List of all :class:`AgentAssignment` instances created.
        """
        all_assignments: list[AgentAssignment] = []

        for step in plan.steps:
            assignment = self._assign_step(step, agent_ctx, selector)
            if assignment is not None:
                all_assignments.append(assignment)
                # Store assignment in step metadata
                step.metadata["agent_assignment"] = assignment.to_dict()
                # Update required_agents with the real agent ID/name
                agent_info = agent_ctx.get_agent(assignment.agent_id)
                if agent_info is not None:
                    if agent_info.name not in step.required_agents:
                        step.required_agents.append(agent_info.name)

        return all_assignments

    def _assign_step(
        self,
        step: PlanStep,
        agent_ctx: AgentPlanningContext,
        selector: AgentSelector,
    ) -> AgentAssignment | None:
        """Assign an agent to a single step.

        Returns:
            An :class:`AgentAssignment`, or ``None`` if no agent could be
            found (logged as warning, not raised).
        """
        # Strategy 1: Direct match from required_agents
        if step.required_agents:
            matches: list[tuple[AgentInfo, float, str]] = []  # (agent, score, cap)
            title_lower = step.title.lower()
            desc_lower = step.description.lower()
            step_text = title_lower + " " + desc_lower

            for agent_name in step.required_agents:
                agent_info = agent_ctx.get_agent(agent_name)
                if agent_info is not None and agent_info.available:
                    # Score this agent on each capability it maps to,
                    # and keep the best score
                    caps = self._mapper.map_capability(agent_name)
                    best_score = -1.0
                    best_cap = "planning"
                    for cap in caps:
                        s = selector.score_agent(agent_info, cap)
                        if s > best_score:
                            best_score = s
                            best_cap = cap
                    # If no mapper caps, score on a generic fallback
                    if best_score < 0:
                        best_score = selector.score_agent(agent_info, "planning")
                        best_cap = "planning"

                    # Contextual boost: if the rule engine suggested this
                    # agent (lowercase name with underscores), and the
                    # agent's type keyword appears in the step title
                    # or description, apply a significant boost.
                    # This ensures "frontend_agent" wins on steps titled
                    # "Diseñar interfaz" even if BackendAgent has higher
                    # raw priority.
                    if "_" in agent_name and agent_name.islower():
                        # Extract the type keyword: "frontend_agent" → "frontend"
                        type_keyword = agent_name.rsplit("_", 1)[0]
                        if type_keyword in step_text:
                            best_score = min(best_score + 0.30, 1.0)

                    matches.append((agent_info, best_score, best_cap))

            if matches:
                # Pick the highest-scoring match
                matches.sort(key=lambda x: -x[1])
                best_agent, best_score, best_cap = matches[0]
                assignment = AgentAssignment(
                    step_id=step.id,
                    agent_id=best_agent.id,
                    capability=best_cap,
                    confidence_score=best_score,
                    reason=(
                        f"Direct match: rule engine suggested "
                        f"'{best_agent.name}' (score: {best_score:.2f})"
                    ),
                )
                return assignment

        # Strategy 2: Capability-based resolution
        # Determine capabilities from step metadata or title keywords
        capabilities = self._resolve_step_capabilities(step)

        for cap in capabilities:
            try:
                assignment = selector.select_agent(
                    capability=cap,
                    step_id=step.id,
                    required_agents=step.required_agents or None,
                )
                return assignment
            except (CapabilityMissingError, AgentNotFoundError):
                continue

        # Strategy 3: Fallback — find the best available agent
        available = agent_ctx.list_available()
        if available:
            # Pick the highest-priority available agent
            best = max(available, key=lambda a: a.priority)
            score = selector.score_agent(best, "planning")
            return AgentAssignment(
                step_id=step.id,
                agent_id=best.id,
                capability="planning",
                confidence_score=score,
                reason=(
                    f"Fallback: no capability match, selected highest-priority "
                    f"agent '{best.name}' (score: {score:.2f})"
                ),
            )

        # No agents at all — log warning and return None
        logger.warning(
            "No agents available for step '%s' (%s)",
            step.title,
            step.id,
        )
        return None

    def _resolve_step_capabilities(self, step: PlanStep) -> list[str]:
        """Resolve which capabilities a step needs.

        Checks step metadata for explicit capabilities, then falls back
        to keyword-based resolution via the capability mapper.

        Args:
            step: The plan step.

        Returns:
            Deduplicated list of capability strings.
        """
        caps: list[str] = []

        # Check explicit metadata
        if "required_capabilities" in step.metadata:
            explicit = step.metadata["required_capabilities"]
            if isinstance(explicit, list):
                caps.extend(explicit)

        # Map step title keywords to capabilities
        if step.required_agents:
            for agent_name in step.required_agents:
                caps.extend(self._mapper.map_capability(agent_name))

        # Try resolving the step title as a requirement
        title_caps = self._mapper.resolve_requirement(step.title)
        caps.extend(title_caps)

        # Also check description for capability hints
        desc_caps = self._mapper.resolve_requirement(step.description)
        caps.extend(desc_caps)

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for c in caps:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped
