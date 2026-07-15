"""Agent planning context — represents available agents for planning.

Sprint 3.3 — Agent-Aware Planning Integration.

:class:`AgentPlanningContext` is a lightweight snapshot of the agent registry
that the planner uses during plan generation.  It decouples the planner from
the live :class:`AgentRegistry` (which is async) and provides a synchronous,
duck-typed view of registered agents, their capabilities, states, and metadata.

The context is **not** a live proxy — it is populated once (via
:meth:`AgentPlanningContext.from_registry` or manual calls to
:meth:`add_agent`) and then consumed read-only during plan generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# AgentInfo — lightweight, synchronous agent description
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Synchronous snapshot of a registered agent.

    Designed to be decoupled from :class:`multiagent.registry.AgentRecord`.
    Contains only the fields relevant for planning decisions, avoiding the
    need to import async-heavy multiagent types inside the planner.

    Attributes:
        id: Unique agent identifier (UUID or name).
        name: Human-readable agent name.
        capabilities: List of capability strings the agent provides
            (e.g. ``["code.generate", "filesystem.write"]``).
        status: Current agent status string
            (e.g. ``"idle"``, ``"created"``, ``"busy"``).
        priority: Agent priority (higher = preferred).
        metadata: Extensible key-value store.
        tools: List of tool names associated with the agent.
        available: Whether the agent is available for selection.
    """

    id: str = ""
    name: str = ""
    capabilities: list[str] = field(default_factory=list)
    status: str = "created"
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    available: bool = True

    # -- convenience ---------------------------------------------------------

    def provides(self, capability: str) -> bool:
        """Check whether this agent provides a capability.

        Supports exact match and parent-to-child hierarchical match
        (e.g. agent with ``"code"`` can satisfy ``"code.generate"``).
        This mirrors the behaviour of :class:`AgentRecord.provides`
        in the multiagent registry.

        Args:
            capability: Capability name to check.

        Returns:
            ``True`` if the agent provides the capability.
        """
        for cap in self.capabilities:
            if cap == capability:
                return True
            # Hierarchical: agent has "code" → can satisfy "code.generate"
            if capability.startswith(cap + "."):
                return True
        return False

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "id": self.id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "priority": self.priority,
            "metadata": dict(self.metadata),
            "tools": list(self.tools),
            "available": self.available,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentInfo:
        """Construct an :class:`AgentInfo` from a plain dict."""
        raw: dict[str, Any] = dict(data)
        raw["capabilities"] = list(raw.get("capabilities", []))
        raw["tools"] = list(raw.get("tools", []))
        raw["metadata"] = dict(raw.get("metadata", {}))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# AgentPlanningContext
# ---------------------------------------------------------------------------


@dataclass
class AgentPlanningContext:
    """Snapshot of available agents for plan generation.

    The context is populated once and consumed synchronously during the
    planning pipeline.  It provides CRUD operations on :class:`AgentInfo`
    entries and capability-based lookup methods.

    Typical lifecycle::

        ctx = AgentPlanningContext.from_registry(agent_registry)
        agents_for_code = ctx.find_by_capability("code.generate")
        ctx.remove_agent("dead_agent_id")

    Attributes:
        agents: Ordered list of :class:`AgentInfo` snapshots.
        metadata: Extensible key-value store for context-level data.
    """

    agents: list[AgentInfo] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- CRUD ----------------------------------------------------------------

    def add_agent(self, info: AgentInfo) -> None:
        """Add an agent to the context.

        If an agent with the same ``id`` already exists it is replaced.

        Args:
            info: The agent snapshot to add.
        """
        for i, existing in enumerate(self.agents):
            if existing.id == info.id:
                self.agents[i] = info
                return
        self.agents.append(info)

    def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent by its ID or name (normalized matching).

        Args:
            agent_id: The agent identifier to remove.

        Returns:
            ``True`` if an agent was removed, ``False`` otherwise.
        """
        norm = self._normalize(agent_id)
        for i, info in enumerate(self.agents):
            if info.id == agent_id or self._normalize(info.name) == norm:
                self.agents.pop(i)
                return True
        return False

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize an agent name for comparison.

        Strips underscores and lowercases to bridge Sprint 2's
        ``BackendAgent`` with Sprint 3.2's ``backend_agent``.
        """
        return name.lower().replace("_", "")

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Look up an agent by ID or name.

        Name matching is case-insensitive and ignores underscores
        to bridge the gap between Sprint 2's ``AgentRecord.name``
        (e.g. ``"FrontendAgent"``) and Sprint 3.2's rule engine
        suggestions (e.g. ``"frontend_agent"``).

        Args:
            agent_id: Agent identifier (UUID or name).

        Returns:
            The matching :class:`AgentInfo`, or ``None`` if not found.
        """
        norm = self._normalize(agent_id)
        for info in self.agents:
            if info.id == agent_id or self._normalize(info.name) == norm:
                return info
        return None

    def find_by_capability(self, capability: str) -> list[AgentInfo]:
        """Find agents that provide a given capability.

        Results are sorted by priority (descending) and then by
        the order they were added.

        Args:
            capability: Capability name or hierarchical prefix.

        Returns:
            Sorted list of matching :class:`AgentInfo` instances.
        """
        matches = [
            info for info in self.agents if info.available and info.provides(capability)
        ]
        matches.sort(key=lambda a: (-a.priority, self.agents.index(a)))
        return matches

    def list_available(self) -> list[AgentInfo]:
        """Return all agents that are marked as available.

        Returns:
            List of available :class:`AgentInfo` instances.
        """
        return [info for info in self.agents if info.available]

    def list_capabilities(self) -> list[str]:
        """Return a sorted, deduplicated list of all capabilities.

        Returns:
            Sorted list of unique capability strings across all agents.
        """
        seen: set[str] = set()
        caps: list[str] = []
        for info in self.agents:
            for cap in info.capabilities:
                if cap not in seen:
                    seen.add(cap)
                    caps.append(cap)
        return sorted(caps)

    @property
    def agent_count(self) -> int:
        """Number of agents in the context."""
        return len(self.agents)

    @property
    def available_count(self) -> int:
        """Number of available agents."""
        return sum(1 for a in self.agents if a.available)

    # -- factory -------------------------------------------------------------

    @classmethod
    def from_registry(cls, registry: Any) -> AgentPlanningContext:
        """Build a context from a live :class:`AgentRegistry`.

        This is the primary bridge between Sprint 2's async registry and
        Sprint 3.3's synchronous planning context.  It calls the registry's
        synchronous discovery methods to extract agent snapshots.

        The method uses duck-typing — it only requires the registry to
        expose ``get_all()`` and ``find_by_capability_simple()`` (or
        ``list_agents()``), which are synchronous methods on
        :class:`AgentRegistry`.

        Args:
            registry: An :class:`AgentRegistry` instance.

        Returns:
            A populated :class:`AgentPlanningContext`.
        """
        ctx = cls()

        # Duck-typed: try get_all() first, then list_agents()
        records = []
        if hasattr(registry, "get_all"):
            records = registry.get_all()
        elif hasattr(registry, "list_agents"):
            records = registry.list_agents()

        for record in records:
            info = cls._record_to_info(record)
            ctx.add_agent(info)

        return ctx

    @staticmethod
    def _record_to_info(record: Any) -> AgentInfo:
        """Convert an :class:`AgentRecord` to an :class:`AgentInfo`.

        Uses duck-typing to extract fields without importing
        :class:`multiagent.registry.AgentRecord`.

        Args:
            record: An object with ``id``, ``name``, ``provided_capabilities``,
                ``status``, ``priority``, ``metadata``, ``is_available``, and
                optionally ``tools`` attributes.

        Returns:
            A lightweight :class:`AgentInfo` snapshot.
        """
        tools: list[str] = []
        if hasattr(record, "agent") and hasattr(record.agent, "tools"):
            tools = list(getattr(record.agent, "tools", []))
        elif hasattr(record, "tools"):
            tools = list(record.tools)  # type: ignore[union-attr]

        return AgentInfo(
            id=getattr(record, "id", ""),
            name=getattr(record, "name", ""),
            capabilities=list(getattr(record, "provided_capabilities", [])),
            status=(
                getattr(record, "status", "created").value
                if hasattr(getattr(record, "status", ""), "value")
                else str(getattr(record, "status", "created"))
            ),
            priority=getattr(record, "priority", 0),
            metadata=dict(getattr(record, "metadata", {})),
            tools=tools,
            available=getattr(record, "is_available", True),
        )

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "agents": [a.to_dict() for a in self.agents],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentPlanningContext:
        """Construct an :class:`AgentPlanningContext` from a plain dict."""
        agents = [AgentInfo.from_dict(a) for a in data.get("agents", [])]
        return cls(agents=agents, metadata=dict(data.get("metadata", {})))
