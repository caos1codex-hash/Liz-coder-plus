"""Multiagent subsystem initializer.

Creates, configures and registers all 7 specialized multiagent agents
(Coder, Git, Memory, Planner, Research, Reviewer, Terminal) into the
core Orchestrator via the MultiagentAdapter.

Also installs keyword-based routing rules in the AgentRouter so that
user messages matching specific patterns are dispatched to the most
appropriate specialized agent. The LLM agent remains the default for
all unmatched messages.

Sprint 8 — Multiagent System Integration.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# Pre-compiled patterns for routing to specialized agents.
# Each entry: (agent_name, compiled_pattern, description)
_ROUTING_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # Terminal — direct command execution requests.
    (
        "terminal",
        re.compile(
            r"\b(ejecuta|ejecutar|run|corr[eo]\b|terminal|bash|cmd|"
            r"shell|powershell)\b.*",
            re.IGNORECASE,
        ),
        "terminal execution request",
    ),
    # Git — version control operations.
    (
        "git",
        re.compile(
            r"\b(git\s+(status|log|diff|add|commit|push|pull|branch|"
            r"merge|checkout|clone|stash|fetch|rebase|reset|show))\b",
            re.IGNORECASE,
        ),
        "git operation",
    ),
    # Reviewer — code review requests.
    (
        "reviewer",
        re.compile(
            r"\b(revisa|review|revisar|auditar|auditor[ií]a|"
            r"c[óo]digo\s+(seguro|quality|calidad))\b",
            re.IGNORECASE,
        ),
        "code review request",
    ),
    # Research — documentation lookup and investigation.
    (
        "research",
        re.compile(
            r"\b(busca|investiga|documentaci[óo]n|API|librer[ií]a|"
            r"framework|busquedar|b[úu]squeda)\b",
            re.IGNORECASE,
        ),
        "research request",
    ),
    # Memory — memory management requests.
    (
        "memory",
        re.compile(
            r"\b(recuerda|memoria|guarda\s+en\s+memoria|olvida|"
            r"recuperar\s+memoria|memory|store|retrieve)\b",
            re.IGNORECASE,
        ),
        "memory request",
    ),
    # Coder — code generation, refactoring, fixes.
    (
        "coder",
        re.compile(
            r"\b(escribe|crea|genera|refactoriza|implementa|"
            r"corrige|fix|test|c[óo]digo|programa|funci[óo]n|"
            r"clase|m[óo]dulo|script)\b",
            re.IGNORECASE,
        ),
        "code generation request",
    ),
    # Planner — planning and task decomposition.
    (
        "planner",
        re.compile(
            r"\b(planifica|plan|descompon|organiza|estrategia|"
            r"workflow|pasos|tareas|roadmap|planifica)\b",
            re.IGNORECASE,
        ),
        "planning request",
    ),
]


async def _matches_pattern(message: str, pattern: re.Pattern[str]) -> bool:
    """Check if a message matches a routing pattern.

    Args:
        message: The user message.
        pattern: Compiled regex pattern.

    Returns:
        True if the pattern matches anywhere in the message.
    """
    return bool(pattern.search(message))


async def initialize_multiagent_system(orchestrator: Orchestrator) -> None:
    """Initialize the full multiagent system and wire it to the Orchestrator.

    This function:
      1. Adds the multiagent package to sys.path.
      2. Instantiates all 7 specialized agents.
      3. Creates an AgentOrchestrator for multi-agent coordination.
      4. Wraps each agent in a MultiagentAdapter (core Protocol).
      5. Registers each adapter in the core Orchestrator.

    Agents created:
      - coder: Writes, refactors, fixes, tests code.
      - git: Git operations (commit, branch, merge, push, pull).
      - memory: Short/long memory, embeddings, vector search.
      - planner: Task decomposition, workflow generation with DAG.
      - research: Documentation lookup, API search, URL fetching.
      - reviewer: Code review with quality heuristics.
      - terminal: Shell execution with safety patterns.

    Args:
        orchestrator: The core Orchestrator instance.
    """
    try:
        # Add multiagent package to sys.path.
        repo_root = Path(__file__).resolve().parents[3]
        multiagent_pkg = repo_root / "packages" / "multiagent" / "src"

        if str(multiagent_pkg) not in sys.path:
            sys.path.insert(0, str(multiagent_pkg))

        # Import multiagent components.
        from multiagent import (  # type: ignore[import-untyped]
            CoderAgent,
            GitAgent,
            MemoryAgent,
            PlannerAgent,
            ResearchAgent,
            ReviewerAgent,
            TerminalAgent,
        )
        from multiagent.orchestrator import AgentOrchestrator  # type: ignore[import-untyped]
        from multiagent.config import MultiAgentConfig  # type: ignore[import-untyped]
        from multiagent.messages import MessageBus  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.warning(
            "Multiagent package not available — skipping multi-agent init: %s",
            exc,
        )
        return

    try:
        # Import our adapter.
        from src.adapters.multiagent_adapter import MultiagentAdapter

        # 1. Create the multiagent shared infrastructure.
        message_bus = MessageBus()
        config = MultiAgentConfig()

        # 2. Create the AgentOrchestrator for internal coordination.
        ma_orchestrator = AgentOrchestrator(
            config=config,
            message_bus=message_bus,
        )

        # 3. Instantiate all 7 specialized agents.
        specialized_agents = [
            CoderAgent(message_bus=message_bus),
            GitAgent(message_bus=message_bus),
            MemoryAgent(message_bus=message_bus),
            PlannerAgent(message_bus=message_bus),
            ResearchAgent(message_bus=message_bus),
            ReviewerAgent(message_bus=message_bus),
            TerminalAgent(message_bus=message_bus),
        ]

        # 4. Register each agent in the multiagent orchestrator.
        registered_count = 0
        for agent in specialized_agents:
            try:
                await ma_orchestrator.register_agent(agent)
                registered_count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to register multiagent '%s': %s",
                    agent.name, exc,
                )

        # 5. Wrap each agent in a core-compatible adapter and register
        #    in the core Orchestrator alongside LLMAgent.
        core_registered = 0
        for agent in specialized_agents:
            adapter = MultiagentAdapter(agent)
            try:
                orchestrator.register_agent(adapter)
                core_registered += 1
                logger.debug(
                    "Registered multiagent adapter: %s → core Orchestrator",
                    agent.name,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to register adapter for '%s': %s",
                    agent.name, exc,
                )

        # 6. Install routing rules in the AgentRouter.
        #    Rules are checked in order; first match wins.
        #    The LLM agent remains the default for unmatched messages.
        routing_rules_added = 0
        for agent_name, pattern, description in _ROUTING_RULES:
            try:
                orchestrator.router.add_rule(
                    agent_name,
                    lambda msg, ctx, p=pattern: _matches_pattern(msg, p),
                )
                routing_rules_added += 1
            except KeyError:
                logger.debug(
                    "Skipping routing rule for '%s' — agent not registered",
                    agent_name,
                )

        # 7. Store the multiagent orchestrator for later use.
        orchestrator._multiagent_orchestrator = ma_orchestrator  # type: ignore[attr-defined]

        logger.info(
            "Multiagent system initialized: %d agent(s) in MA orchestrator, "
            "%d adapter(s) in core Orchestrator, %d routing rule(s) installed",
            registered_count,
            core_registered,
            routing_rules_added,
        )

    except Exception:
        logger.exception(
            "Failed to initialize multiagent system. "
            "Continuing with single LLMAgent."
        )
