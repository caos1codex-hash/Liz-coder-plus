"""Multiagent subsystem initializer.

Creates, configures and registers all 7 specialized multiagent agents
(Coder, Git, Memory, Planner, Research, Reviewer, Terminal) into the
core Orchestrator via the MultiagentAdapter.

Sprint 8 — Multiagent System Integration.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


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

        # 6. Store the multiagent orchestrator for later use.
        orchestrator._multiagent_orchestrator = ma_orchestrator  # type: ignore[attr-defined]

        logger.info(
            "Multiagent system initialized: %d agent(s) in MA orchestrator, "
            "%d adapter(s) in core Orchestrator",
            registered_count,
            core_registered,
        )

    except Exception:
        logger.exception(
            "Failed to initialize multiagent system. "
            "Continuing with single LLMAgent."
        )
