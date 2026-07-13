"""Core orchestrator.

The orchestrator is the central hub of Liz Coder Plus. It receives user
input from the API layer, decides which agent(s) to invoke, coordinates
memory access, dispatches tools, and emits events. This is a foundation
stub - the real implementation arrives in Sprint 2.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.config import AppConfig

logger = logging.getLogger(__name__)


class Orchestrator:
    """Central coordinator for agents, memory, and tools.

    Sprint 1 - Prompt 1: foundation only. Method bodies will be
    implemented in Sprint 2 (agents + memory) and Sprint 3 (tools).
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        logger.info("Orchestrator initialized (stage: scaffolding)")

    @property
    def config(self) -> AppConfig:
        """Return the active configuration."""
        return self._config

    async def handle_message(self, message: str) -> dict[str, Any]:
        """Handle an incoming user message.

        Args:
            message: The raw user input.

        Returns:
            A response dictionary. Currently a stub.
        """
        logger.debug("Received message: %s", message)
        return {
            "status": "not_implemented",
            "message": "Orchestrator is in scaffolding stage (Sprint 1 - Prompt 1).",
        }
