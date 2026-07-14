"""Integration adapters between the communication package and existing modules (Sprint 2.9).

This module provides thin adapters that connect the communication
package to the existing Sprint 2.1-2.8 modules:

- :class:`RegistryAdapter` — wraps an existing ``AgentRegistry`` so the
  communication package can look up agents by name without importing
  the multiagent package at module load time.
- :class:`OrchestratorAdapter` — wraps an existing
  ``WorkflowOrchestrator`` (or ``AgentOrchestrator``) and exposes a
  unified ``delegate`` / ``request_help`` / ``transfer_context`` /
  ``handoff`` API that uses the new :class:`CollaborationManager`
  under the hood.
- :func:`build_integrated_bus` — convenience factory that wires the
  :class:`AgentMessageBus` to an existing ``MessageBus``,
  ``ExecutionRepository``, and ``EventBus``.
- :func:`build_integrated_manager` — convenience factory that wires the
  :class:`CollaborationManager` to an existing ``AgentMessageBus``.

The adapters are **optional**: the communication package works
standalone. They are imported lazily so the package has no hard
dependencies on multiagent / memory / workflow at import time.
"""

from __future__ import annotations

import logging
from typing import Any

from .bus import AgentMessageBus, BusConfig
from .collaboration import CollaborationManager
from .protocol import AgentMessage
from .repository import IMessageRepository, InMemoryMessageRepository, MessageRepository

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# RegistryAdapter
# ----------------------------------------------------------------------


class RegistryAdapter:
    """Adapter over an existing ``AgentRegistry``.

    The wrapped registry must expose ``get(name) -> AgentRecord | None``
    and ``get_all() -> list[AgentRecord]`` (provided by Sprint 2.2-2's
    :class:`multiagent.registry.AgentRegistry`).
    """

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def get_agent(self, name: str) -> Any | None:
        """Return the agent record with ``name``, or None."""
        try:
            return self._registry.get(name)
        except Exception:  # noqa: BLE001
            return None

    def list_agents(self) -> list[Any]:
        """Return all registered agent records."""
        try:
            return self._registry.get_all()
        except Exception:  # noqa: BLE001
            return []

    def agent_exists(self, name: str) -> bool:
        return self.get_agent(name) is not None

    def agent_names(self) -> list[str]:
        records = self.list_agents()
        names: list[str] = []
        for r in records:
            name = getattr(r, "name", None) or getattr(r, "id", "")
            names.append(str(name))
        return names


# ----------------------------------------------------------------------
# OrchestratorAdapter
# ----------------------------------------------------------------------


class OrchestratorAdapter:
    """Adapter that exposes the new communication API on top of an
    existing orchestrator.

    Wraps either a ``WorkflowOrchestrator`` (Sprint 2.2) or an
    ``AgentOrchestrator`` (Sprint 2.1). When the underlying orchestrator
    already exposes ``delegate`` / ``request_help`` /
    ``transfer_context``, this adapter delegates to them; otherwise it
    falls back to using the :class:`CollaborationManager` directly.
    """

    def __init__(
        self,
        orchestrator: Any,
        *,
        manager: CollaborationManager | None = None,
        registry: RegistryAdapter | None = None,
    ) -> None:
        self._orch = orchestrator
        self._manager = manager
        self._registry = registry

    @property
    def manager(self) -> CollaborationManager | None:
        return self._manager

    @property
    def orchestrator(self) -> Any:
        return self._orch

    async def delegate(
        self,
        from_agent: str,
        to_agent: str,
        *,
        task: dict[str, Any] | None = None,
        task_id: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Delegate a task from one agent to another.

        If the underlying orchestrator has a ``delegate`` method, it is
        called first (so existing behavior is preserved). The
        collaboration is then tracked via the
        :class:`CollaborationManager` (if configured).

        Returns the :class:`Collaboration` handle (or the orchestrator's
        raw return value if no manager is configured).
        """
        orch_result: Any = None
        if hasattr(self._orch, "delegate"):
            try:
                orch_result = await self._orch.delegate(
                    from_agent,
                    to_agent,
                    task or {},
                    workflow_id=task_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("orchestrator.delegate failed")
        if self._manager is None:
            return orch_result
        collab = await self._manager.delegate(
            from_agent,
            to_agent,
            task_id=task_id,
            payload=task,
            timeout=timeout,
        )
        return collab

    async def request_help(
        self,
        from_agent: str,
        topic: str,
        *,
        task_id: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Broadcast a help request."""
        if hasattr(self._orch, "request_help"):
            try:
                await self._orch.request_help(from_agent, topic, workflow_id=task_id)
            except Exception:  # noqa: BLE001
                logger.exception("orchestrator.request_help failed")
        if self._manager is None:
            return None
        return await self._manager.request_help(
            from_agent,
            topic,
            task_id=task_id,
            timeout=timeout,
        )

    async def transfer_context(
        self,
        from_agent: str,
        to_agent: str,
        *,
        context: dict[str, Any],
        task_id: str | None = None,
    ) -> Any:
        """Transfer context from one agent to another."""
        if hasattr(self._orch, "transfer_context"):
            try:
                await self._orch.transfer_context(
                    task_id or "",
                    from_agent,
                    to_agent,
                )
            except Exception:  # noqa: BLE001
                logger.exception("orchestrator.transfer_context failed")
        if self._manager is None:
            return None
        return await self._manager.transfer_context(
            from_agent,
            to_agent,
            context=context,
            task_id=task_id,
        )

    async def handoff(
        self,
        from_agent: str,
        to_agent: str,
        *,
        task_id: str | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Hand off a task from one agent to another."""
        if self._manager is None:
            return None
        return await self._manager.handoff(
            from_agent,
            to_agent,
            task_id=task_id,
            reason=reason,
            payload=payload,
        )

    async def wait_for_response(
        self,
        message_id: str,
        *,
        timeout: float | None = None,
    ) -> AgentMessage | None:
        """Wait for a response to ``message_id``."""
        if self._manager is None:
            return None
        return await self._manager.wait_for_response(message_id, timeout=timeout)


# ----------------------------------------------------------------------
# Factories
# ----------------------------------------------------------------------


def build_integrated_bus(
    *,
    message_bus: Any | None = None,
    execution_repository: Any | None = None,
    event_bus: Any | None = None,
    config: BusConfig | None = None,
) -> AgentMessageBus:
    """Build an :class:`AgentMessageBus` wired to existing modules.

    Args:
        message_bus:          Existing ``multiagent.messages.MessageBus``
                              (Sprint 2.1). If None, a standalone bus
                              is created.
        execution_repository: Existing
                              ``memory.repositories.ExecutionRepository``
                              (Sprint 1.8). If None, an
                              :class:`InMemoryMessageRepository` is used.
        event_bus:            Existing ``multiagent.workflow.EventBus``
                              (Sprint 2.2). If None, no event-bus
                              bridging is performed.
        config:               Optional :class:`BusConfig`.
    """
    repository: IMessageRepository
    if execution_repository is not None:
        repository = MessageRepository(execution_repository)
    else:
        repository = InMemoryMessageRepository()
    cfg = config or BusConfig()
    if event_bus is not None:
        cfg.bridge_to_event_bus = True
    return AgentMessageBus(
        bus=message_bus,
        repository=repository,
        event_bus=event_bus,
        config=cfg,
    )


def build_integrated_manager(
    *,
    bus: AgentMessageBus | None = None,
    message_bus: Any | None = None,
    execution_repository: Any | None = None,
    event_bus: Any | None = None,
    default_timeout: float = 30.0,
) -> tuple[AgentMessageBus, CollaborationManager]:
    """Build a :class:`CollaborationManager` wired to existing modules.

    Returns a ``(bus, manager)`` tuple. If ``bus`` is provided, it is
    reused; otherwise a new integrated bus is built via
    :func:`build_integrated_bus`.
    """
    if bus is None:
        bus = build_integrated_bus(
            message_bus=message_bus,
            execution_repository=execution_repository,
            event_bus=event_bus,
        )
    manager = CollaborationManager(bus=bus, default_timeout=default_timeout)
    return bus, manager


__all__ = [
    "OrchestratorAdapter",
    "RegistryAdapter",
    "build_integrated_bus",
    "build_integrated_manager",
]
