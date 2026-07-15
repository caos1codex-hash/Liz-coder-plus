"""Sprint 3.5 — Planner ↔ Agent intelligence bridge.

Improves communication between the :class:`Planner` and the
:class:`AgentRegistry` so that:

* **Suitable agents are selected** based on required capabilities
  (already partially done by Sprint 3.3's
  :class:`~planner.agent_selector.AgentSelector`).
* **Agents are validated to exist** before execution (already done
  by :class:`~planner.executor.PlannerExecutor._validate_plan_assignments`).
* **Missing capabilities can be requested** dynamically, via
  :class:`CapabilityRequestBroker`.
* **Failure recovery decisions** are surfaced to the planner so it
  can re-plan or escalate (see
  :class:`~planner.strategy.FailureRecoveryEngine`).

This module wires all four concerns together without introducing new
dependencies on the multiagent package — the bridge works against
the duck-typed :class:`AgentPlanningContext`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .agent_context import AgentInfo, AgentPlanningContext
from .exceptions import CapabilityMissingError
from .strategy_enums import CapabilityRequestStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CapabilityRequest
# ---------------------------------------------------------------------------


@dataclass
class CapabilityRequest:
    """A request for a missing capability.

    Attributes:
        id: Unique request identifier.
        capability: The capability string being requested.
        step_id: The plan step that needs it (``""`` if plan-level).
        priority: Request urgency (0 = low, 10 = critical).
        status: Current :class:`CapabilityRequestStatus`.
        created_at: UTC timestamp.
        fulfilled_at: UTC timestamp when the request was fulfilled,
            or ``""`` if still pending.
        fulfilled_by_agent_id: ID of the agent that satisfied the
            request, or ``""`` if not yet fulfilled.
        reason: Human-readable explanation.
    """

    id: str = ""
    capability: str = ""
    step_id: str = ""
    priority: int = 5
    status: CapabilityRequestStatus = CapabilityRequestStatus.PENDING
    created_at: str = ""
    fulfilled_at: str = ""
    fulfilled_by_agent_id: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "capability": self.capability,
            "step_id": self.step_id,
            "priority": self.priority,
            "status": self.status.value,
            "created_at": self.created_at,
            "fulfilled_at": self.fulfilled_at,
            "fulfilled_by_agent_id": self.fulfilled_by_agent_id,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# CapabilityRequestBroker
# ---------------------------------------------------------------------------


class CapabilityRequestBroker:
    """Track and resolve requests for missing capabilities.

    The broker maintains an in-memory registry of pending requests.
    When a new agent is registered (via :meth:`check_new_agent`),
    any matching pending requests are fulfilled.

    The broker is **synchronous** — it doesn't wait for agents to
    appear.  Callers poll :meth:`pending_requests` or check
    :meth:`is_fulfilled` to drive their workflow.
    """

    def __init__(self) -> None:
        self._requests: dict[str, CapabilityRequest] = {}
        self._context: AgentPlanningContext | None = None

    def attach_context(self, context: AgentPlanningContext) -> None:
        """Attach an :class:`AgentPlanningContext` for capability lookups.

        The context is used to check whether a capability is now
        available.  Call :meth:`refresh_context` whenever the
        underlying registry changes.
        """
        self._context = context

    def refresh_context(self, context: AgentPlanningContext) -> None:
        """Replace the attached context (e.g. after registry changes)."""
        self._context = context
        # Re-evaluate all pending requests.
        for req in list(self._requests.values()):
            if req.status == CapabilityRequestStatus.PENDING:
                self._try_fulfill(req)

    def request(
        self,
        capability: str,
        *,
        step_id: str = "",
        priority: int = 5,
        reason: str = "",
    ) -> CapabilityRequest:
        """Raise a request for *capability*.

        If the capability is already available in the attached
        context, the request is created in FULFILLED state.

        Returns:
            The created :class:`CapabilityRequest`.
        """
        req = CapabilityRequest(
            id=uuid.uuid4().hex[:16],
            capability=capability,
            step_id=step_id,
            priority=max(0, min(priority, 10)),
            status=CapabilityRequestStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )
        self._requests[req.id] = req
        # Try to fulfill immediately if context has it.
        self._try_fulfill(req)
        return req

    def check_new_agent(self, agent: AgentInfo) -> list[CapabilityRequest]:
        """Check if *agent* fulfills any pending requests.

        Args:
            agent: A newly-registered :class:`AgentInfo`.

        Returns:
            List of requests that were fulfilled by this agent
            (possibly empty).
        """
        fulfilled: list[CapabilityRequest] = []
        for req in self._requests.values():
            if req.status != CapabilityRequestStatus.PENDING:
                continue
            if agent.provides(req.capability):
                req.status = CapabilityRequestStatus.FULFILLED
                req.fulfilled_at = datetime.now(timezone.utc).isoformat()
                req.fulfilled_by_agent_id = agent.id
                fulfilled.append(req)
        return fulfilled

    def get_request(self, request_id: str) -> CapabilityRequest | None:
        """Return a request by ID, or ``None``."""
        return self._requests.get(request_id)

    def pending_requests(self) -> list[CapabilityRequest]:
        """Return all requests still in PENDING status."""
        return [
            r for r in self._requests.values()
            if r.status == CapabilityRequestStatus.PENDING
        ]

    def fulfilled_requests(self) -> list[CapabilityRequest]:
        """Return all requests in FULFILLED status."""
        return [
            r for r in self._requests.values()
            if r.status == CapabilityRequestStatus.FULFILLED
        ]

    def deny(self, request_id: str, *, reason: str = "") -> bool:
        """Manually deny a request (e.g. user rejected).

        Returns ``True`` if the request existed and was pending.
        """
        req = self._requests.get(request_id)
        if req is None or req.status != CapabilityRequestStatus.PENDING:
            return False
        req.status = CapabilityRequestStatus.DENIED
        req.reason = reason or "denied by caller"
        return True

    def expire(self, *, older_than_seconds: int = 300) -> int:
        """Mark pending requests older than *older_than_seconds* as TIMEOUT.

        Returns the number of requests timed out.
        """
        now = datetime.now(timezone.utc)
        expired_count = 0
        for req in self._requests.values():
            if req.status != CapabilityRequestStatus.PENDING:
                continue
            try:
                created = datetime.fromisoformat(req.created_at)
            except (ValueError, TypeError):
                continue
            age = (now - created).total_seconds()
            if age > older_than_seconds:
                req.status = CapabilityRequestStatus.TIMEOUT
                expired_count += 1
        return expired_count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _try_fulfill(self, req: CapabilityRequest) -> None:
        if self._context is None:
            return
        agents = self._context.find_by_capability(req.capability)
        if agents:
            req.status = CapabilityRequestStatus.FULFILLED
            req.fulfilled_at = datetime.now(timezone.utc).isoformat()
            req.fulfilled_by_agent_id = agents[0].id


# ---------------------------------------------------------------------------
# PlannerAgentBridge
# ---------------------------------------------------------------------------


class PlannerAgentBridge:
    """High-level bridge between the planner and the agent context.

    Provides:

    * :meth:`validate_plan_capabilities` — checks every step's
      required capabilities against the agent context.
    * :meth:`request_missing_capabilities` — raises
      :class:`CapabilityRequest`s for any missing capability.
    * :meth:`select_agents_for_plan` — wraps the Sprint 3.3
      :class:`~planner.agent_selector.AgentSelector` and produces
      per-step assignments with capability validation.
    * :meth:`agent_summary` — returns a JSON-serialisable summary
      of which agents cover which capabilities.

    Args:
        context: The :class:`AgentPlanningContext` (snapshot of the
            live registry).
        broker: Optional :class:`CapabilityRequestBroker`.  If
            omitted, one is created and attached to *context*.
    """

    def __init__(
        self,
        context: AgentPlanningContext,
        *,
        broker: CapabilityRequestBroker | None = None,
    ) -> None:
        self._context = context
        self._broker = broker or CapabilityRequestBroker()
        self._broker.attach_context(context)

    @property
    def context(self) -> AgentPlanningContext:
        return self._context

    @property
    def broker(self) -> CapabilityRequestBroker:
        return self._broker

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_plan_capabilities(
        self,
        plan: Any,
    ) -> dict[str, list[str]]:
        """Check which required capabilities are missing for *plan*.

        Args:
            plan: A :class:`TaskPlan` (duck-typed: needs ``.steps``
                with each step having ``.metadata`` and ``.id``).

        Returns:
            Dict ``{step_id: [missing_capability, ...]}``.  Empty
            dict means all capabilities are covered.
        """
        missing: dict[str, list[str]] = {}
        for step in plan.steps:
            caps = step.metadata.get("required_capabilities", []) or []
            if not caps:
                continue
            missing_caps = [
                cap for cap in caps
                if not self._context.find_by_capability(cap)
            ]
            if missing_caps:
                missing[step.id] = missing_caps
        return missing

    def request_missing_capabilities(
        self,
        plan: Any,
        *,
        priority: int = 5,
    ) -> list[CapabilityRequest]:
        """Raise :class:`CapabilityRequest`s for every missing capability.

        Args:
            plan: The plan to scan.
            priority: Urgency for the requests.

        Returns:
            List of created :class:`CapabilityRequest` instances.
            Already-fulfilled requests (capability was added since
            the last call) are included with status=FULFILLED.
        """
        missing_map = self.validate_plan_capabilities(plan)
        requests: list[CapabilityRequest] = []
        for step_id, caps in missing_map.items():
            for cap in caps:
                req = self._broker.request(
                    capability=cap,
                    step_id=step_id,
                    priority=priority,
                    reason=f"Plan step requires '{cap}'",
                )
                requests.append(req)
        return requests

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select_agents_for_plan(
        self,
        plan: Any,
        *,
        selector: Any | None = None,
    ) -> dict[str, Any]:
        """Select agents for each step of *plan*.

        Args:
            plan: The plan to assign agents to.
            selector: Optional pre-configured
                :class:`~planner.agent_selector.AgentSelector`.  If
                omitted, one is created with the default
                :class:`~planner.capability_mapper.CapabilityMapper`.

        Returns:
            Dict ``{step_id: AgentAssignment.to_dict()}`` for steps
            where an agent was found.  Steps with no matching agent
            are omitted (their required capabilities are surfaced
            via :meth:`request_missing_capabilities`).
        """
        from .agent_selector import AgentSelector
        from .capability_mapper import CapabilityMapper

        sel = selector or AgentSelector(
            self._context, CapabilityMapper.create_default()
        )

        assignments: dict[str, Any] = {}
        for step in plan.steps:
            caps = step.metadata.get("required_capabilities", []) or []
            if not caps:
                # Try to infer from title.
                mapper = CapabilityMapper.create_default()
                caps = mapper.resolve_requirement(step.title) or []
            if not caps:
                continue
            # Try each capability until one resolves.
            for cap in caps:
                try:
                    assignment = sel.select_agent(
                        capability=cap,
                        step_id=step.id,
                        required_agents=step.required_agents or None,
                    )
                    assignments[step.id] = assignment.to_dict()
                    break
                except (CapabilityMissingError, Exception):  # noqa: BLE001
                    continue
        return assignments

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def agent_summary(self) -> dict[str, Any]:
        """Return a summary of the agent context.

        Includes:
        * Total / available agent counts.
        * Capability → agent count mapping.
        * Per-capability top agent.
        """
        cap_map: dict[str, list[str]] = {}
        for agent in self._context.agents:
            for cap in agent.capabilities:
                cap_map.setdefault(cap, []).append(agent.name)
        # Pick top agent per capability (highest priority).
        top_per_cap: dict[str, str] = {}
        for cap, agents in cap_map.items():
            matches = self._context.find_by_capability(cap)
            if matches:
                top_per_cap[cap] = matches[0].name
        return {
            "total_agents": self._context.agent_count,
            "available_agents": self._context.available_count,
            "capabilities": sorted(cap_map.keys()),
            "capability_coverage": {
                cap: len(agents) for cap, agents in cap_map.items()
            },
            "top_agent_per_capability": top_per_cap,
        }

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def suggest_escalation_agent(
        self,
        failed_step_capability: str,
        *,
        exclude_agent_ids: set[str] | None = None,
    ) -> AgentInfo | None:
        """Suggest an agent for escalation after a step failure.

        Picks an agent that:
        * Provides the same (or a parent) capability.
        * Is not in *exclude_agent_ids* (i.e. not the one that just
          failed).
        * Has the highest priority among the remaining candidates.

        Returns ``None`` if no candidate is found.
        """
        exclude = exclude_agent_ids or set()
        candidates = [
            a for a in self._context.find_by_capability(failed_step_capability)
            if a.id not in exclude and a.available
        ]
        if candidates:
            return candidates[0]
        # Try parent capability.
        parent = failed_step_capability.split(".")[0]
        if parent != failed_step_capability:
            candidates = [
                a for a in self._context.find_by_capability(parent)
                if a.id not in exclude and a.available
            ]
            if candidates:
                return candidates[0]
        return None


__all__ = [
    "CapabilityRequest",
    "CapabilityRequestBroker",
    "PlannerAgentBridge",
]
