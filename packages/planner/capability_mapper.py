"""Capability mapper — translates plan needs to registry capabilities.

Sprint 3.3 — Agent-Aware Planning Integration.

:class:`CapabilityMapper` bridges the gap between the planner's textual
agent names (e.g. ``"frontend_agent"`` from Sprint 3.2's rule engine) and
the multiagent system's capability strings (e.g. ``"code.generate"``).

It maintains bidirectional mappings:

* **agent_name → capabilities**: which capabilities a named agent should have.
* **requirement → capabilities**: which capability keywords map to what
  capabilities in the registry.
* **capability → agent_name**: reverse lookup for verification.

The mapper is configurable — callers can extend the default mappings via
:meth:`add_agent_mapping` and :meth:`add_requirement_mapping`.
"""

from __future__ import annotations


class CapabilityMapper:
    """Translate between planner-level needs and registry-level capabilities.

    The mapper is used by :class:`AgentSelector` to determine which agents
    are suitable for a given planning step.

    Args:
        agent_mappings: Initial ``{agent_name: [capabilities]}`` dict.
        requirement_mappings: Initial ``{requirement_keyword: [capabilities]}``
            dict.
    """

    def __init__(
        self,
        agent_mappings: dict[str, list[str]] | None = None,
        requirement_mappings: dict[str, list[str]] | None = None,
    ) -> None:
        # agent_name → [capability_strings]
        self._agent_mappings: dict[str, list[str]] = dict(agent_mappings or {})
        # requirement keyword → [capability_strings]
        self._requirement_mappings: dict[str, list[str]] = dict(
            requirement_mappings or {}
        )

    # -- configuration -------------------------------------------------------

    def add_agent_mapping(self, agent_name: str, capabilities: list[str]) -> None:
        """Register capabilities for a planner-level agent name.

        If the agent name already has mappings they are replaced.

        Args:
            agent_name: The planner's agent name
                (e.g. ``"frontend_agent"``).
            capabilities: List of capability strings the agent should have
                (e.g. ``["code.generate", "filesystem.write"]``).
        """
        self._agent_mappings[agent_name] = list(capabilities)

    def add_requirement_mapping(
        self, requirement: str, capabilities: list[str]
    ) -> None:
        """Register which capabilities satisfy a requirement keyword.

        For example, ``"crear interfaz web"`` maps to ``["code.generate"]``.

        Args:
            requirement: A requirement keyword or phrase.
            capabilities: List of capability strings that satisfy this
                requirement.
        """
        self._requirement_mappings[requirement] = list(capabilities)

    # -- query ----------------------------------------------------------------

    def map_capability(self, agent_name: str) -> list[str]:
        """Get the capabilities associated with a planner agent name.

        Args:
            agent_name: The agent name from the planner's context.

        Returns:
            List of capability strings, or an empty list if unknown.
        """
        return list(self._agent_mappings.get(agent_name, []))

    def resolve_requirement(self, requirement: str) -> list[str]:
        """Resolve a requirement keyword to capability strings.

        Tries exact match first, then falls back to substring matching
        against all registered requirement keywords.

        Args:
            requirement: A requirement description or keyword.

        Returns:
            Deduplicated list of matching capability strings.
        """
        result: list[str] = []

        # Exact match
        if requirement in self._requirement_mappings:
            result.extend(self._requirement_mappings[requirement])

        # Substring / keyword match
        req_lower = requirement.lower()
        for keyword, caps in self._requirement_mappings.items():
            if keyword.lower() in req_lower and keyword != requirement:
                result.extend(caps)

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for cap in result:
            if cap not in seen:
                seen.add(cap)
                deduped.append(cap)
        return deduped

    def get_required_capabilities(self, agent_names: list[str]) -> list[str]:
        """Get the union of capabilities required by a list of agent names.

        Args:
            agent_names: List of planner-level agent names.

        Returns:
            Deduplicated, sorted list of all required capabilities.
        """
        caps: set[str] = set()
        for name in agent_names:
            caps.update(self._agent_mappings.get(name, []))
        return sorted(caps)

    def get_agent_names(self) -> list[str]:
        """Return all registered agent names.

        Returns:
            Sorted list of agent names.
        """
        return sorted(self._agent_mappings.keys())

    def get_requirements(self) -> list[str]:
        """Return all registered requirement keywords.

        Returns:
            Sorted list of requirement keywords.
        """
        return sorted(self._requirement_mappings.keys())

    # -- factory -------------------------------------------------------------

    @classmethod
    def create_default(cls) -> CapabilityMapper:
        """Create a mapper pre-loaded with standard agent-capability mappings.

        These mappings connect the Sprint 3.2 rule engine's agent names
        with the Sprint 2 registry's capability strings.

        Returns:
            A :class:`CapabilityMapper` with standard defaults.
        """
        mapper = cls()

        # Agent name → capabilities (maps Sprint 3.2 agent names
        # to Sprint 2 capability strings)
        mapper.add_agent_mapping(
            "planning_agent",
            [
                "planning",
                "workflow.create",
                "plan",
                "decompose",
            ],
        )
        mapper.add_agent_mapping(
            "frontend_agent",
            [
                "code.generate",
                "filesystem.read",
                "filesystem.write",
            ],
        )
        mapper.add_agent_mapping(
            "backend_agent",
            [
                "code.generate",
                "code.test",
                "filesystem.read",
                "filesystem.write",
            ],
        )
        mapper.add_agent_mapping(
            "tester_agent",
            [
                "code.test",
                "code.review",
            ],
        )
        mapper.add_agent_mapping(
            "review_agent",
            [
                "code.review",
                "audit",
            ],
        )
        mapper.add_agent_mapping(
            "research_agent",
            [
                "research",
                "web.search",
                "documentation.lookup",
            ],
        )
        mapper.add_agent_mapping(
            "docs_agent",
            [
                "documentation.lookup",
                "documentation",
            ],
        )
        mapper.add_agent_mapping(
            "devops_agent",
            [
                "terminal.execute",
                "execute",
                "shell",
            ],
        )
        mapper.add_agent_mapping(
            "git_agent",
            [
                "git.commit",
                "git.branch",
                "git.push",
                "git.pull",
            ],
        )
        mapper.add_agent_mapping(
            "security_agent",
            [
                "code.review",
                "audit",
            ],
        )
        mapper.add_agent_mapping(
            "database_agent",
            [
                "code.generate",
                "code.test",
            ],
        )

        # Requirement keyword → capabilities
        # English
        mapper.add_requirement_mapping(
            "frontend", ["code.generate", "filesystem.write"]
        )
        mapper.add_requirement_mapping("backend", ["code.generate", "code.test"])
        mapper.add_requirement_mapping("api", ["code.generate"])
        mapper.add_requirement_mapping("database", ["code.generate"])
        mapper.add_requirement_mapping("test", ["code.test"])
        mapper.add_requirement_mapping("testing", ["code.test"])
        mapper.add_requirement_mapping("deploy", ["terminal.execute"])
        mapper.add_requirement_mapping("devops", ["terminal.execute", "execute"])
        mapper.add_requirement_mapping("git", ["git.commit", "git.push"])
        mapper.add_requirement_mapping("security", ["code.review", "audit"])
        mapper.add_requirement_mapping("auth", ["code.generate", "code.review"])
        mapper.add_requirement_mapping("documentation", ["documentation.lookup"])
        mapper.add_requirement_mapping("research", ["research", "web.search"])
        mapper.add_requirement_mapping("review", ["code.review"])
        # Spanish
        mapper.add_requirement_mapping("interfaz web", ["code.generate"])
        mapper.add_requirement_mapping("api rest", ["code.generate"])
        mapper.add_requirement_mapping("base de datos", ["code.generate"])
        mapper.add_requirement_mapping("despliegue", ["terminal.execute"])
        mapper.add_requirement_mapping("seguridad", ["code.review", "audit"])
        mapper.add_requirement_mapping(
            "autenticación", ["code.generate", "code.review"]
        )
        mapper.add_requirement_mapping("documentación", ["documentation.lookup"])

        return mapper
