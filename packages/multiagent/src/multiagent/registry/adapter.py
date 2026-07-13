"""Backward Compatibility Adapter (Sprint 2.2/2).

Traduce nombres de agentes concretos (PlannerAgent, CoderAgent, etc.)
a capabilities equivalentes, para que workflows antiguos que referencian
agentes por nombre sigan funcionando con el nuevo sistema basado en
capabilities.

Mapeo:

    PlannerAgent    → planning, workflow.create
    CoderAgent      → code.generate, code.refactor, code.fix, code.test,
                       filesystem.read, filesystem.write
    ReviewerAgent   → code.review
    ResearchAgent   → research, web.search, documentation.lookup
    TerminalAgent   → terminal.execute
    GitAgent        → git.commit, git.branch, git.merge, git.push, git.pull
    MemoryAgent     → memory.retrieve, memory.store, memory.search

El Adapter también mapea `step.action` a capabilities:

    refactor        → code.refactor
    review          → code.review
    test            → code.test
    fix             → code.fix
    write           → code.generate + filesystem.write
    plan            → planning
    plan_workflow   → planning + workflow.create
    status          → git.status
    commit          → git.commit
    branch          → git.branch
    merge           → git.merge
    push            → git.push
    pull            → git.pull
    store           → memory.store
    retrieve        → memory.retrieve
    search          → memory.search
    execute         → terminal.execute
    gather          → research
    analyze         → research
    summarize       → research.summarize
    query           → terminal.execute

Si un workflow antiguo tiene `step.agent = "PlannerAgent"`, el Adapter
traduce a `capability = "planning"` y el resolver busca cualquier agente
que provea `planning` (no necesariamente el PlannerAgent concreto).
"""

from __future__ import annotations

import logging
from typing import Any

from .agent_registry import AgentRegistry
from .resolver import CapabilityResolver, ResolutionResult

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Mapeos estáticos
# ----------------------------------------------------------------------


# Nombre de agente concreto → capabilities equivalentes.
AGENT_NAME_TO_CAPABILITIES: dict[str, list[str]] = {
    "PlannerAgent": ["planning", "workflow.create"],
    "planner": ["planning", "workflow.create"],
    "CoderAgent": [
        "code.generate", "code.refactor", "code.fix", "code.test",
        "filesystem.read", "filesystem.write",
    ],
    "coder": [
        "code.generate", "code.refactor", "code.fix", "code.test",
        "filesystem.read", "filesystem.write",
    ],
    "ReviewerAgent": ["code.review"],
    "reviewer": ["code.review"],
    "ResearchAgent": ["research", "web.search", "documentation.lookup"],
    "research": ["research", "web.search", "documentation.lookup"],
    "TerminalAgent": ["terminal.execute"],
    "terminal": ["terminal.execute"],
    "GitAgent": [
        "git.commit", "git.branch", "git.merge", "git.push", "git.pull",
        "git.status",
    ],
    "git": [
        "git.commit", "git.branch", "git.merge", "git.push", "git.pull",
        "git.status",
    ],
    "MemoryAgent": ["memory.retrieve", "memory.store", "memory.search"],
    "memory": ["memory.retrieve", "memory.store", "memory.search"],
}


# Action de step → capability principal (la que se usa para resolver).
ACTION_TO_CAPABILITY: dict[str, str] = {
    # Planner
    "plan": "planning",
    "plan_workflow": "planning",
    "decompose": "planning",
    "handle": "planning",  # fallback del planner

    # Coder
    "write": "code.generate",
    "refactor": "code.refactor",
    "fix": "code.fix",
    "test": "code.test",
    "code": "code.generate",

    # Reviewer
    "review": "code.review",
    "audit": "code.review",

    # Research
    "gather": "research",
    "analyze": "research",
    "summarize": "research.summarize",
    "research": "research",

    # Terminal
    "execute": "terminal.execute",
    "cmd": "terminal.execute",
    "command": "terminal.execute",
    "query": "terminal.execute",
    "validate": "terminal.execute",

    # Git
    "status": "git.status",
    "diff": "git.status",
    "log": "git.status",
    "add": "git.commit",
    "commit": "git.commit",
    "branch": "git.branch",
    "checkout": "git.branch",
    "merge": "git.merge",
    "push": "git.push",
    "pull": "git.pull",

    # Memory
    "store": "memory.store",
    "retrieve": "memory.retrieve",
    "forget": "memory.store",
    "add_embedding": "memory.store",
    "search": "memory.search",
    "recent": "memory.retrieve",
    "clear": "memory.store",
    "keys": "memory.retrieve",
}


# ----------------------------------------------------------------------
# Adapter
# ----------------------------------------------------------------------


class BackwardCompatibilityAdapter:
    """Adapta workflows antiguos (que usan nombres de agentes) al nuevo
    sistema basado en capabilities.

    Uso::

        adapter = BackwardCompatibilityAdapter(
            registry=registry, resolver=resolver,
        )
        # step.agent = "PlannerAgent", step.action = "refactor"
        result = await adapter.resolve_step_agent(step)
        # result.resolved == True, result.agent_record.agent es el agente
    """

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        resolver: CapabilityResolver,
    ) -> None:
        self._registry = registry
        self._resolver = resolver

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    @property
    def resolver(self) -> CapabilityResolver:
        return self._resolver

    # ------------------------------------------------------------------
    # Traducción
    # ------------------------------------------------------------------

    @staticmethod
    def agent_name_to_capabilities(name: str) -> list[str]:
        """Traduce un nombre de agente concreto a capabilities."""
        # Case-insensitive lookup.
        caps = AGENT_NAME_TO_CAPABILITIES.get(name)
        if caps is not None:
            return list(caps)
        caps = AGENT_NAME_TO_CAPABILITIES.get(name.lower())
        if caps is not None:
            return list(caps)
        # Si no hay mapeo, devolver el nombre como capability (puede ser
        # un agente custom ya registrado con su propia capability).
        return [name.lower().replace("agent", "")]

    @staticmethod
    def action_to_capability(action: str) -> str:
        """Traduce un action de step a una capability."""
        if not action:
            return "planning"  # fallback seguro
        cap = ACTION_TO_CAPABILITY.get(action)
        if cap is not None:
            return cap
        cap = ACTION_TO_CAPABILITY.get(action.lower())
        if cap is not None:
            return cap
        # Si no hay mapeo, devolver el action como capability.
        return action.lower()

    # ------------------------------------------------------------------
    # Resolución
    # ------------------------------------------------------------------

    async def resolve_step_agent(
        self,
        step: Any,
        *,
        exclude: set[str] | None = None,
    ) -> ResolutionResult:
        """Resuelve el agente para un step antiguo.

        Prioridad:

        1. Si `step.agent` es un nombre concreto mapeado (PlannerAgent,
           CoderAgent, etc.), traducir a capabilities y resolver.
        2. Si `step.action` está mapeado, usar esa capability.
        3. Si `step.agent` está registrado en el registry (por nombre),
           usarlo directamente (compatibilidad con agentes custom).
        4. Fallback: resolver con capability "planning".

        Args:
            step:    Step con `.agent` y `.action`.
            exclude: Nombres de agentes a excluir.
        """
        exclude = exclude or set()
        agent_name = getattr(step, "agent", "") or ""
        action = getattr(step, "action", "") or ""

        # 1. Si agent es un nombre concreto mapeado, traducir.
        if agent_name and (
            agent_name in AGENT_NAME_TO_CAPABILITIES
            or agent_name.lower() in AGENT_NAME_TO_CAPABILITIES
        ):
            caps = self.agent_name_to_capabilities(agent_name)
            # Si hay action mapeada, usar esa capability (más específica).
            if action:
                action_cap = self.action_to_capability(action)
                # Verificar que la action_cap esté en las caps del agente.
                if any(
                    self._cap_matches_provided(action_cap, cap)
                    for cap in caps
                ):
                    return await self._resolver.resolve(action_cap, exclude=exclude)
            # Si no, resolver con la primera capability.
            return await self._resolver.resolve(caps[0], exclude=exclude)

        # 2. Si agent está registrado por nombre, usar directamente.
        if agent_name and self._registry.exists(agent_name):
            record = self._registry.get(agent_name)
            if record is not None and record.is_available:
                return ResolutionResult(
                    capability="direct_lookup",
                    agent_record=record,
                    score=1.0,
                    resolved=True,
                    reason=f"direct lookup by name '{agent_name}'",
                )

        # 3. Si hay action mapeada, usar esa capability.
        if action:
            action_cap = self.action_to_capability(action)
            return await self._resolver.resolve(action_cap, exclude=exclude)

        # 4. Fallback.
        return await self._resolver.resolve("planning", exclude=exclude)

    def _cap_matches_provided(self, target: str, provided: str) -> bool:
        """True si `target` matchea `provided` (con wildcards/jerarquía)."""
        if target == provided:
            return True
        if provided.endswith(".*"):
            prefix = provided[:-2]
            return target == prefix or target.startswith(prefix + ".")
        if target.startswith(provided + "."):
            return True
        return False

    # ------------------------------------------------------------------
    # Registro automático de agentes concretos
    # ------------------------------------------------------------------

    async def auto_register_legacy_agents(
        self,
        agents: list,
    ) -> list:
        """Registra una lista de agentes concretos con capabilities automáticas.

        Útil para migrar código existente que instancia agentes concretos
        directamente (e.g., `PlannerAgent()`).

        Args:
            agents: Lista de instancias de BaseAgent (PlannerAgent,
                    CoderAgent, etc.).

        Returns:
            Lista de AgentRecord creados.
        """
        records = []
        for agent in agents:
            caps = self.agent_name_to_capabilities(agent.name)
            try:
                record = await self._registry.register(
                    agent,
                    provided_capabilities=caps,
                    replace_existing=True,
                )
                records.append(record)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to auto-register legacy agent '%s'", agent.name
                )
        return records


__all__ = [
    "ACTION_TO_CAPABILITY",
    "AGENT_NAME_TO_CAPABILITIES",
    "BackwardCompatibilityAdapter",
]
