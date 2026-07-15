"""Sprint 3.5 — Execution Strategy Layer.

Decides **how** each step (or batch of steps) should be executed,
based on:

* The step's capability requirements.
* The dependency graph (parallel batches, critical path).
* Past execution outcomes (retries, escalations).
* Runtime availability of agents.

The strategy layer is **planner-side**: it produces
:class:`StepExecutionPlan` and :class:`PlanExecutionStrategy`
objects that the :class:`~planner.executor.PlannerExecutor`
consumes.  It does **not** perform the actual execution — that
remains the executor's responsibility.

Key concepts
------------

* :class:`ExecutionDecision` — per-step decision: mode, retry budget,
  escalation target, human-confirm flag.
* :class:`PlanExecutionStrategy` — plan-level strategy: maps step IDs
  to decisions, records the chosen global policy (sequential /
  parallel / mixed).
* :class:`ExecutionStrategyEngine` — the engine that builds a strategy
  from a :class:`TaskPlan` + :class:`DependencyGraph` + agent
  context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .dependency_graph import DependencyGraph, ParallelBatch
from .enums import Priority
from .models import PlanStep, TaskPlan
from .strategy_enums import ExecutionMode, FailureDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExecutionDecision:
    """Per-step execution decision.

    Attributes:
        step_id: The plan step this decision applies to.
        mode: :class:`ExecutionMode` (sequential / parallel / ...).
        batch_index: The parallel-batch index this step belongs to
            (0 if sequential).  Steps in the same batch can run
            concurrently.
        retry_budget: Maximum retries before escalation.
        escalation_target: Capability to escalate to on failure
            (``""`` = no escalation).
        requires_human_confirm: If ``True``, the executor must
            pause before running this step and wait for explicit
            user approval.
        failure_decision: Default :class:`FailureDecision` if the
            step fails after exhausting retries.
        timeout_hint: Suggested timeout in seconds (0 = use step
            default).
        rationale: Human-readable explanation of the decision.
    """

    step_id: str = ""
    mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    batch_index: int = 0
    retry_budget: int = 1
    escalation_target: str = ""
    requires_human_confirm: bool = False
    failure_decision: FailureDecision = FailureDecision.RETRY
    timeout_hint: float = 0.0
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "mode": self.mode.value,
            "batch_index": self.batch_index,
            "retry_budget": self.retry_budget,
            "escalation_target": self.escalation_target,
            "requires_human_confirm": self.requires_human_confirm,
            "failure_decision": self.failure_decision.value,
            "timeout_hint": self.timeout_hint,
            "rationale": self.rationale,
        }


@dataclass
class PlanExecutionStrategy:
    """Plan-level execution strategy.

    Attributes:
        plan_id: The :attr:`TaskPlan.id` this strategy applies to.
        global_mode: The dominant execution mode for the plan.
        decisions: Mapping ``step_id → ExecutionDecision``.
        batches: Parallel batches (computed by the strategy engine).
        max_parallelism: Maximum number of concurrent steps.
        estimated_total_duration: Sum of batch durations (parallel
            execution time).  ``0.0`` if durations are unknown.
        critical_path_step_ids: Step IDs on the critical path.
        requires_any_human_confirm: ``True`` if any decision sets
            ``requires_human_confirm``.
    """

    plan_id: str = ""
    global_mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    decisions: dict[str, ExecutionDecision] = field(default_factory=dict)
    batches: list[ParallelBatch] = field(default_factory=list)
    max_parallelism: int = 1
    estimated_total_duration: float = 0.0
    critical_path_step_ids: list[str] = field(default_factory=list)
    requires_any_human_confirm: bool = False

    def decision_for(self, step_id: str) -> ExecutionDecision | None:
        """Look up the decision for *step_id*, or ``None``."""
        return self.decisions.get(step_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "global_mode": self.global_mode.value,
            "decisions": {sid: d.to_dict() for sid, d in self.decisions.items()},
            "batches": [b.to_dict() for b in self.batches],
            "max_parallelism": self.max_parallelism,
            "estimated_total_duration": self.estimated_total_duration,
            "critical_path_step_ids": list(self.critical_path_step_ids),
            "requires_any_human_confirm": self.requires_any_human_confirm,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ExecutionStrategyEngine:
    """Build a :class:`PlanExecutionStrategy` from a plan + graph.

    The engine is **stateless**: each call to :meth:`build` produces
    a fresh strategy.  State about past failures is passed in via
    *failure_history*.

    Args:
        max_retries: Default retry budget per step.  Default 1.
        critical_retry_threshold: When the critical path's retry
            count exceeds this, switch the failure_decision to
            ESCALATE.  Default 2.
        parallel_safe_capabilities: Capabilities whose steps can
            safely run in parallel (e.g. ``{"testing", "research"}``).
            Steps with capabilities outside this set are forced to
            SEQUENTIAL mode even if the graph would allow parallel.
        human_confirm_capabilities: Capabilities that always require
            human confirmation (e.g. ``{"security", "devops"}``).
    """

    def __init__(
        self,
        *,
        max_retries: int = 1,
        critical_retry_threshold: int = 2,
        parallel_safe_capabilities: set[str] | None = None,
        human_confirm_capabilities: set[str] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._critical_retry_threshold = critical_retry_threshold
        self._parallel_safe = parallel_safe_capabilities or {
            "testing",
            "research",
            "documentation",
            "review",
        }
        self._human_confirm = human_confirm_capabilities or {
            "security",
            "devops",
            "deployment",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        plan: TaskPlan,
        graph: DependencyGraph | None = None,
        *,
        failure_history: dict[str, int] | None = None,
    ) -> PlanExecutionStrategy:
        """Build an execution strategy for *plan*.

        Args:
            plan: The :class:`TaskPlan` to strategise.
            graph: Optional pre-built :class:`DependencyGraph`.  If
                omitted, a fresh one is built from *plan*.
            failure_history: Optional mapping ``step_id → past
                failure count``.  Used to bump retry budgets and
                trigger escalations.

        Returns:
            A :class:`PlanExecutionStrategy`.
        """
        if graph is None:
            graph = DependencyGraph(plan)

        failure_history = failure_history or {}

        # Compute parallel batches (raises on cycle; we let it propagate).
        batches = graph.parallel_batches()
        critical = graph.critical_path()
        critical_set = set(critical.step_ids)

        decisions: dict[str, ExecutionDecision] = {}
        any_human_confirm = False

        # Map step_id → batch index.
        step_to_batch: dict[str, int] = {}
        for batch in batches:
            for sid in batch.step_ids:
                step_to_batch[sid] = batch.index

        for step in plan.steps:
            decision = self._decide_for_step(
                step=step,
                batch_index=step_to_batch.get(step.id, 0),
                batch_size=len(batches[step_to_batch.get(step.id, 0)].step_ids)
                if step_to_batch.get(step.id, 0) < len(batches) else 1,
                is_critical=step.id in critical_set,
                past_failures=failure_history.get(step.id, 0),
            )
            decisions[step.id] = decision
            if decision.requires_human_confirm:
                any_human_confirm = True

        # Determine global mode.
        global_mode = self._pick_global_mode(batches, decisions)

        # Total duration = sum of batch durations (parallel within batch).
        total_duration = sum(b.estimated_duration for b in batches)
        max_parallelism = max((len(b.step_ids) for b in batches), default=1)

        return PlanExecutionStrategy(
            plan_id=plan.id,
            global_mode=global_mode,
            decisions=decisions,
            batches=batches,
            max_parallelism=max_parallelism,
            estimated_total_duration=total_duration,
            critical_path_step_ids=list(critical.step_ids),
            requires_any_human_confirm=any_human_confirm,
        )

    # ------------------------------------------------------------------
    # Per-step decision
    # ------------------------------------------------------------------

    def _decide_for_step(
        self,
        *,
        step: PlanStep,
        batch_index: int,
        batch_size: int,
        is_critical: bool,
        past_failures: int,
    ) -> ExecutionDecision:
        """Build the :class:`ExecutionDecision` for a single step."""
        caps = step.metadata.get("required_capabilities", []) or []
        # Treat as a set for membership tests.
        caps_set = set(caps)

        # 1. Execution mode.
        if batch_size > 1 and caps_set & self._parallel_safe:
            mode = ExecutionMode.PARALLEL
            mode_rationale = (
                f"Parallel: batch {batch_index} has {batch_size} steps "
                f"and capabilities {sorted(caps_set & self._parallel_safe)} "
                f"are parallel-safe"
            )
        else:
            mode = ExecutionMode.SEQUENTIAL
            mode_rationale = (
                f"Sequential: batch {batch_index} "
                f"({'single step' if batch_size == 1 else 'non-parallel-safe capabilities'})"
            )

        # 2. Retry budget.
        retry_budget = self._max_retries
        if is_critical:
            # Critical-path steps get one extra retry.
            retry_budget += 1
        if past_failures > 0:
            # Bump by past failures, capped at 3.
            retry_budget = min(retry_budget + past_failures, 3)

        # 3. Failure decision.
        if past_failures >= self._critical_retry_threshold:
            failure_decision = FailureDecision.ESCALATE
            failure_rationale = (
                f"Escalate: past failures ({past_failures}) "
                f">= threshold ({self._critical_retry_threshold})"
            )
        elif is_critical:
            failure_decision = FailureDecision.RETRY
            failure_rationale = "Retry: critical-path step"
        elif caps_set & {"testing", "documentation"}:
            failure_decision = FailureDecision.SKIP
            failure_rationale = "Skip: non-critical auxiliary step"
        else:
            failure_decision = FailureDecision.RETRY
            failure_rationale = "Retry: default policy"

        # 4. Escalation target.
        escalation_target = ""
        if failure_decision == FailureDecision.ESCALATE:
            # Pick the highest-priority capability not in the step's caps.
            escalation_target = self._pick_escalation_target(caps_set)

        # 5. Human confirmation.
        requires_human_confirm = bool(caps_set & self._human_confirm)
        # Plan-level priority is checked separately by the caller; here
        # we only consider per-step metadata flags.  Step metadata may
        # carry a "priority" hint (integer 0-3) for advanced cases.
        step_priority_hint = step.metadata.get("priority")
        if isinstance(step_priority_hint, (int, float)) and step_priority_hint >= 2:
            requires_human_confirm = True
        elif step.metadata.get("requires_human_confirm") is True:
            requires_human_confirm = True

        # 6. Timeout hint.
        timeout_hint = float(step.estimated_duration or 0.0) * 1.5

        rationale_parts = [mode_rationale, failure_rationale]
        if requires_human_confirm:
            rationale_parts.append("Human confirmation required")
        if escalation_target:
            rationale_parts.append(f"Escalates to '{escalation_target}' on failure")

        return ExecutionDecision(
            step_id=step.id,
            mode=mode,
            batch_index=batch_index,
            retry_budget=retry_budget,
            escalation_target=escalation_target,
            requires_human_confirm=requires_human_confirm,
            failure_decision=failure_decision,
            timeout_hint=timeout_hint,
            rationale="; ".join(rationale_parts),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_global_mode(
        self,
        batches: list[ParallelBatch],
        decisions: dict[str, ExecutionDecision],
    ) -> ExecutionMode:
        """Decide the plan's global mode based on batch shape."""
        if not batches:
            return ExecutionMode.SEQUENTIAL
        parallel_batches = sum(
            1 for b in batches if len(b.step_ids) > 1
        )
        if parallel_batches == 0:
            return ExecutionMode.SEQUENTIAL
        if parallel_batches == len(batches):
            return ExecutionMode.PARALLEL
        # Mixed: some batches parallel, some sequential.
        # Report PARALLEL as the global mode so the executor knows
        # it can use parallel scheduling.
        return ExecutionMode.PARALLEL

    def _pick_escalation_target(self, current_caps: set[str]) -> str:
        """Pick a capability to escalate to.

        Heuristic: prefer "review" if not already present, else
        "planning", else "research".
        """
        for candidate in ("review", "planning", "research"):
            if candidate not in current_caps:
                return candidate
        return ""


# ---------------------------------------------------------------------------
# Failure recovery
# ---------------------------------------------------------------------------


@dataclass
class FailureRecoveryPlan:
    """Action plan for recovering from a failed step.

    Produced by :meth:`ExecutionStrategyEngine.plan_recovery` when
    a step fails.  The executor consumes this to decide what to do
    next.

    Attributes:
        step_id: The failed step.
        decision: What to do (retry / escalate / skip / fail / human).
        next_agent_capability: Capability to use for retry/escalation.
        retry_attempt: Which retry attempt this is (1-based).
        max_retries: Total retries allowed.
        reason: Human-readable explanation.
    """

    step_id: str = ""
    decision: FailureDecision = FailureDecision.RETRY
    next_agent_capability: str = ""
    retry_attempt: int = 0
    max_retries: int = 1
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "decision": self.decision.value,
            "next_agent_capability": self.next_agent_capability,
            "retry_attempt": self.retry_attempt,
            "max_retries": self.max_retries,
            "reason": self.reason,
        }


class FailureRecoveryEngine:
    """Decide recovery actions for failed steps.

    Decoupled from :class:`ExecutionStrategyEngine` so callers can
    use it standalone (e.g. the executor calls it directly when a
    step fails).
    """

    def __init__(
        self,
        *,
        default_max_retries: int = 1,
        escalation_chain: list[str] | None = None,
    ) -> None:
        self._default_max = default_max_retries
        # Default escalation chain: try increasingly senior capabilities.
        self._escalation_chain = escalation_chain or [
            "review",
            "planning",
            "research",
        ]

    def plan_recovery(
        self,
        *,
        step_id: str,
        step_capability: str,
        retry_count: int,
        strategy: PlanExecutionStrategy | None = None,
    ) -> FailureRecoveryPlan:
        """Plan the recovery action for a failed step.

        Args:
            step_id: The failed step's ID.
            step_capability: The capability the step required.
            retry_count: How many times the step has already been
                retried (0 = first failure).
            strategy: Optional plan-level strategy for context
                (e.g. to look up the decision for this step).

        Returns:
            A :class:`FailureRecoveryPlan` describing what to do next.
        """
        decision = FailureDecision.RETRY
        max_retries = self._default_max
        next_cap = step_capability
        reason = ""

        if strategy is not None:
            step_decision = strategy.decision_for(step_id)
            if step_decision is not None:
                max_retries = step_decision.retry_budget
                decision = step_decision.failure_decision
                if step_decision.escalation_target:
                    next_cap = step_decision.escalation_target

        # Override based on retry count.
        if retry_count >= max_retries:
            # Exhausted retries.
            if decision == FailureDecision.RETRY:
                # Try to escalate; if escalation chain is exhausted,
                # fail the plan.
                escalated = self._next_escalation_capability(step_capability)
                if escalated:
                    decision = FailureDecision.ESCALATE
                    next_cap = escalated
                    reason = (
                        f"Retries exhausted ({retry_count}/{max_retries}); "
                        f"escalating to '{next_cap}'"
                    )
                else:
                    decision = FailureDecision.FAIL_PLAN
                    next_cap = ""
                    reason = (
                        f"Retries exhausted ({retry_count}/{max_retries}) "
                        f"and escalation chain is empty; failing plan"
                    )
            elif decision == FailureDecision.ESCALATE:
                # Already escalated; try one more level or fail.
                next_cap = self._next_escalation_capability(next_cap)
                if next_cap:
                    reason = (
                        f"Escalation step {retry_count}/{max_retries}: "
                        f"trying '{next_cap}'"
                    )
                else:
                    decision = FailureDecision.FAIL_PLAN
                    reason = "Escalation chain exhausted; failing plan"
            else:
                reason = f"Step policy={decision.value}; retries exhausted"
        else:
            # Still have retries left.
            if decision == FailureDecision.RETRY:
                reason = f"Retry {retry_count + 1}/{max_retries}"
            elif decision == FailureDecision.ESCALATE:
                next_cap = self._next_escalation_capability(step_capability)
                if next_cap:
                    reason = f"Escalate to '{next_cap}' (attempt {retry_count + 1})"
                else:
                    reason = "Escalation chain exhausted; will fail_plan on next retry"
            else:
                reason = f"Apply policy: {decision.value}"

        return FailureRecoveryPlan(
            step_id=step_id,
            decision=decision,
            next_agent_capability=next_cap,
            retry_attempt=retry_count + 1,
            max_retries=max_retries,
            reason=reason,
        )

    def _next_escalation_capability(self, current: str) -> str:
        """Return the next capability in the escalation chain."""
        try:
            idx = self._escalation_chain.index(current)
        except ValueError:
            idx = -1
        if idx + 1 < len(self._escalation_chain):
            return self._escalation_chain[idx + 1]
        return ""


__all__ = [
    "ExecutionDecision",
    "PlanExecutionStrategy",
    "ExecutionStrategyEngine",
    "FailureRecoveryPlan",
    "FailureRecoveryEngine",
]
