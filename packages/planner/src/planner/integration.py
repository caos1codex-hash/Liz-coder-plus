"""Integration adapters between the planner and existing modules (Sprint 2.8).

This module provides thin adapters that connect the planner package to
the existing Sprint 2.1-2.7 modules:

- :class:`MultiAgentRegistryAdapter` — wraps the existing
  :class:`multiagent.registry.AgentRegistry` so the planner's
  :class:`~planner.capability_matcher.CapabilityMatcher` can use it as
  an :class:`~planner.capability_matcher.AgentRegistryAdapter`.

- :class:`SemanticMemoryAdapter` — wraps the existing
  :class:`multiagent.semantic.SemanticRetrievalEngine` so the planner
  can retrieve similar past plans from semantic memory.

- :class:`WorkflowEngineAdapter` — adapts a plan to the existing
  :class:`multiagent.workflow.WorkflowEngine` so it can be executed by
  the existing workflow runtime (in addition to the planner's own
  :class:`~planner.scheduler.Scheduler`).

- :func:`build_integrated_planner` — convenience factory that wires
  everything together.

The adapters are **optional**: the planner works standalone. They are
imported lazily (only when the caller actually constructs them) so that
the planner package can be imported in environments where the
multiagent package is not available.

The integration respects the existing architecture (Sprint 2.1-2.7):
the planner does NOT replace the workflow engine or the orchestrator;
it provides plans that those components can consume.
"""

from __future__ import annotations

import logging
from typing import Any

from .capability_matcher import AgentInfo, AgentRegistryAdapter
from .exceptions import PlannerError
from .execution_plan import ExecutionPlan
from .planner import PlannerConfig, TaskPlanner

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# MultiAgentRegistryAdapter
# ----------------------------------------------------------------------


class MultiAgentRegistryAdapter(AgentRegistryAdapter):
    """Adapter that wraps an existing ``AgentRegistry``.

    The wrapped registry must expose:

    - ``get_all() -> list[AgentRecord]``
    - ``find_by_capability(capability, only_available=True) -> list[AgentRecord]``
    - ``get(id_or_name) -> AgentRecord | None``

    These methods are provided by
    :class:`multiagent.registry.AgentRegistry` (Sprint 2.2-2).
    """

    def __init__(self, registry: Any) -> None:
        super().__init__(agents=[])
        self._registry = registry
        self._refresh()

    def _refresh(self) -> None:
        """Pull the latest agents from the wrapped registry."""
        try:
            records = self._registry.get_all()
        except Exception:  # noqa: BLE001
            logger.exception("failed to list agents from registry")
            records = []
        self._agents = {}
        for record in records:
            try:
                info = _record_to_info(record)
                # Key by the AgentInfo.id (which is the human-readable
                # name) so callers can look up agents by name.
                self._agents[info.id] = info
            except Exception:  # noqa: BLE001
                logger.warning("skipping agent record %r", record)

    def list_agents(self) -> list[AgentInfo]:
        # Refresh on every call so the matcher always sees the latest
        # workload / availability. This is O(N) but cheap (N is small).
        self._refresh()
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        self._refresh()
        return self._agents.get(agent_id)

    def find_by_capability(self, capability: str) -> list[AgentInfo]:
        self._refresh()
        return super().find_by_capability(capability)

    def update_workload(self, agent_id: str, *, delta: int) -> None:
        # Workload is tracked by the underlying registry; ignore local
        # updates so we don't fight with its bookkeeping.
        pass


def _record_to_info(record: Any) -> AgentInfo:
    """Convert a ``AgentRecord`` to an :class:`AgentInfo`.

    Uses ``record.name`` as the agent id (human-readable) so that the
    planner's matcher produces user-friendly assignments like
    ``assigned_agent='coder'`` instead of UUIDs.

    Note: we use ``_safe_float`` instead of ``or`` for ``success_rate``
    because ``0.0 or 1.0`` evaluates to ``1.0``, which would clobber a
    legitimate 0% success rate (an agent that has failed every task).
    """
    # AgentRecord provides: id (UUID), name, provided_capabilities,
    # priority, is_healthy, is_available, usage_count, success_rate,
    # avg_latency_ms, metadata.
    sr = getattr(record, "success_rate", None)
    return AgentInfo(
        id=getattr(record, "name", None) or record.id,
        name=getattr(record, "name", record.id),
        capabilities=list(getattr(record, "provided_capabilities", []) or []),
        priority=int(getattr(record, "priority", 0) or 0),
        healthy=bool(_safe_attr(record, "is_healthy", default=True)),
        available=bool(_safe_attr(record, "is_available", default=True)),
        usage_count=int(getattr(record, "usage_count", 0) or 0),
        success_rate=float(sr) if sr is not None else 1.0,
        avg_latency_ms=float(getattr(record, "avg_latency_ms", 0.0) or 0.0),
        metadata=dict(getattr(record, "metadata", {}) or {}),
    )


def _safe_attr(obj: Any, attr: str, *, default: Any) -> Any:
    """Get ``attr`` from ``obj``, calling it if it's a method."""
    val = getattr(obj, attr, default)
    if callable(val):
        try:
            return val()
        except Exception:  # noqa: BLE001
            return default
    return val


# ----------------------------------------------------------------------
# SemanticMemoryAdapter
# ----------------------------------------------------------------------


class SemanticMemoryAdapter:
    """Adapter over the existing ``SemanticRetrievalEngine``.

    Provides a ``retrieve_similar(query, top_k)`` method compatible with
    the planner's :class:`~planner.interfaces.ISemanticMemoryAdapter`
    Protocol. Calls into the existing async retrieval engine and
    returns plain dicts.

    The wrapped engine must expose::

        async def retrieve(request: RetrievalRequest) -> RetrievalResult

    where ``RetrievalRequest`` accepts ``query`` and ``top_k``.
    """

    def __init__(
        self,
        engine: Any,
        *,
        namespace: str = "plans",
        loop: Any | None = None,
    ) -> None:
        self._engine = engine
        self._namespace = namespace
        self._loop = loop

    def retrieve_similar(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve similar past plans from semantic memory.

        Returns a list of dicts with at least ``id``, ``score`` and
        ``payload`` keys. On any error returns an empty list (the
        planner treats this as best-effort).
        """
        try:
            return self._sync_retrieve(query, top_k=top_k)
        except Exception:  # noqa: BLE001
            logger.exception("semantic retrieval failed")
            return []

    def _sync_retrieve(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Run the async retrieval engine synchronously.

        If we're already inside an event loop, schedule the coroutine
        on a fresh loop in a separate thread to avoid "loop already
        running" errors. Otherwise just run it.
        """
        import asyncio

        coro = self._call_engine(query, top_k=top_k)
        try:
            asyncio.get_running_loop()
            # We're inside a loop — run in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        except RuntimeError:
            return asyncio.run(coro)

    async def _call_engine(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Call the wrapped engine and normalize the result to plain dicts."""
        engine = self._engine
        # The engine may accept a RetrievalRequest or a plain query.
        # Try a few call signatures.
        try:
            from multiagent.semantic.retrieval import RetrievalRequest  # type: ignore

            req = RetrievalRequest(query=query, top_k=top_k)
            result = await engine.retrieve(req)
        except Exception:  # noqa: BLE001
            # Fall back to a direct call.
            result = await engine.retrieve(query, top_k=top_k)
        # Normalize: RetrievalResult has .items or .results.
        items: list[Any] = []
        for attr in ("items", "results", "matches"):
            val = getattr(result, attr, None)
            if val:
                items = list(val)
                break
        out: list[dict[str, Any]] = []
        for item in items:
            out.append(
                {
                    "id": getattr(item, "id", None) or getattr(item, "memory_id", None),
                    "score": float(getattr(item, "score", 0.0) or 0.0),
                    "payload": getattr(item, "payload", None)
                    or getattr(item, "metadata", {})
                    or {},
                }
            )
        return out


# ----------------------------------------------------------------------
# WorkflowEngineAdapter
# ----------------------------------------------------------------------


class WorkflowEngineAdapter:
    """Convert an :class:`ExecutionPlan` into a ``Workflow``.

    The existing :class:`multiagent.workflow.WorkflowEngine` (Sprint
    2.2) executes ``Workflow`` objects containing ``Step`` objects.
    This adapter translates a planner plan into that format so that
    users who already have a workflow runtime can consume planner
    output without changes.

    The adapter is intentionally one-way: planner -> workflow. The
    reverse direction (workflow -> planner) is not needed because the
    planner supersedes the workflow engine's ad-hoc planning.
    """

    @staticmethod
    def to_workflow(plan: ExecutionPlan) -> Any:
        """Build a ``Workflow`` from ``plan``.

        Imports the workflow package lazily so that the planner package
        remains importable without the multiagent package.

        Raises:
            PlannerError: if the workflow package is unavailable or
                the conversion fails.
        """
        try:
            from multiagent.workflow.models import Step, Workflow  # type: ignore
        except ImportError as exc:
            raise PlannerError(
                "multiagent.workflow package is not available",
                details={"reason": str(exc)},
            ) from exc
        try:
            wf = Workflow(name=f"plan-{plan.id[:12]}", description=plan.objective)
            for task in plan.tasks.values():
                # Use the planner task's id as the step id so that
                # task.dependencies (which reference planner task ids)
                # resolve correctly in the workflow DAG. Without this,
                # Step auto-generates a UUID that doesn't match any
                # dependency, breaking the DAG.
                step = Step(
                    id=task.id,
                    name=task.title,
                    agent=task.assigned_agent or "planner",
                    action=task.metadata.get("action", "execute"),
                    depends_on=list(task.dependencies),
                    input={
                        "task_id": task.id,
                        "description": task.description,
                        "required_capabilities": list(task.required_capabilities),
                    },
                )
                wf.add_step(step)
            return wf
        except Exception as exc:
            raise PlannerError(
                f"failed to convert plan to workflow: {exc}",
                details={"plan_id": plan.id},
            ) from exc


# ----------------------------------------------------------------------
# build_integrated_planner
# ----------------------------------------------------------------------


def build_integrated_planner(
    *,
    registry: Any | None = None,
    semantic_engine: Any | None = None,
    config: PlannerConfig | None = None,
    fallback_agent_id: str | None = None,
) -> TaskPlanner:
    """Build a :class:`TaskPlanner` wired to existing modules.

    Args:
        registry:          Existing ``AgentRegistry`` (Sprint 2.2-2).
                           If None, an empty in-memory adapter is used.
        semantic_engine:   Existing ``SemanticRetrievalEngine`` (Sprint 2.7).
                           If None, semantic memory lookup is disabled.
        config:            Optional :class:`PlannerConfig`.
        fallback_agent_id: Optional agent id to use when no agent can
                           be matched (prevents hard failures in
                           degraded mode).

    Returns:
        A :class:`TaskPlanner` with all integrations wired up.
    """
    adapter: AgentRegistryAdapter
    if registry is not None:
        adapter = MultiAgentRegistryAdapter(registry)
    else:
        adapter = AgentRegistryAdapter()

    semantic: SemanticMemoryAdapter | None = None
    if semantic_engine is not None:
        semantic = SemanticMemoryAdapter(semantic_engine)

    from .capability_matcher import CapabilityMatcher

    cfg = config or PlannerConfig()
    matcher = CapabilityMatcher(
        adapter=adapter,
        weights=cfg.matcher,
        fallback_agent_id=fallback_agent_id,
    )

    return TaskPlanner(
        config=cfg,
        matcher=matcher,
        registry_adapter=adapter,
        semantic_memory=semantic,
    )


__all__ = [
    "MultiAgentRegistryAdapter",
    "SemanticMemoryAdapter",
    "WorkflowEngineAdapter",
    "build_integrated_planner",
]
