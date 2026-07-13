"""Core orchestrator.

The orchestrator is the central hub of Liz Coder Plus. It receives user
input from the API layer, decides which agent to invoke (via the
AgentRouter), coordinates session/memory access, dispatches tools, and
emits events.

Sprint 1 - Prompt 3: connected to the persistent memory system.
When a MemoryManager is provided, every user and assistant message
is persisted to SQLite via the SessionManager.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from src.agent_router import AgentRouter, EchoAgent
from src.protocols import Agent, Memory, Tool
from src.session_manager import SessionManager
from src.ws_manager import WebSocketManager

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Central coordinator for agents, memory, sessions and tools.

    Wiring:
      - WebSocketManager  -> tracks live connections
      - SessionManager    -> tracks conversation history per session
      - AgentRouter       -> selects an agent per message
      - MemoryManager     -> persists messages to SQLite (optional)
    """

    def __init__(
        self,
        *,
        ws_manager: WebSocketManager | None = None,
        session_manager: SessionManager | None = None,
        router: AgentRouter | None = None,
    ) -> None:
        self._ws_manager = ws_manager or WebSocketManager()
        self._sessions = session_manager or SessionManager()
        self._router = router or AgentRouter()
        self._agents: dict[str, Agent] = {}
        self._tools: dict[str, Tool] = {}
        self._memory: MemoryManager | None = None

        # Make sure the router's default agent is also registered here.
        default = self._router.default_agent
        self._agents[default.name] = default

        logger.info(
            "Orchestrator initialized (ws=%d sessions=%d agents=%d)",
            self._ws_manager.connection_count,
            self._sessions.session_count,
            len(self._agents),
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def ws_manager(self) -> WebSocketManager:
        return self._ws_manager

    @property
    def session_manager(self) -> SessionManager:
        return self._sessions

    @property
    def router(self) -> AgentRouter:
        return self._router

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(self, agent: Agent) -> None:
        """Register an agent with both the orchestrator and the router."""
        self._agents[agent.name] = agent
        self._router.register_agent(agent)
        logger.debug("Registered agent '%s'", agent.name)

    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the orchestrator."""
        self._tools[tool.name] = tool
        logger.debug("Registered tool '%s'", tool.name)

    def attach_memory(self, memory: MemoryManager) -> None:
        """Attach a persistent memory backend.

        The MemoryManager is wired to the SessionManager so that
        all conversation turns are persisted to SQLite.

        Args:
            memory: An initialized MemoryManager instance.
        """
        self._memory = memory
        self._sessions.attach_memory(memory)
        logger.info("Persistent memory connected to Orchestrator")

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        message: str,
        session_id: str,
        *,
        mode: str = "confirmation",
    ) -> dict[str, Any]:
        """Handle an incoming user message.

        Flow:
            1. Persist user state and the inbound turn.
            2. Build context from session history.
            3. Route to agent.
            4. Record assistant turn.
            5. Return response envelope.

        When a MemoryManager is attached, steps 1 and 4 also persist
        to SQLite via the SessionManager.

        Args:
            message:    Raw user input.
            session_id: Conversation session identifier.
            mode:       Permission mode requested by the client.

        Returns:
            A dict with keys: type, content, status, session_id.
        """
        logger.info(
            "Orchestrator.handle_message session=%s mode=%s len=%d",
            session_id,
            mode,
            len(message),
        )

        # Persist user state and the inbound turn.
        self._sessions.set_mode(session_id, mode)
        await self._sessions.append_turn(
            session_id, role="user", content=message, metadata={"mode": mode}
        )

        context = self._build_context(session_id)

        try:
            agent = await self._router.route(message, context)
            response_text = await agent.handle(message, context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent failed to handle message")
            await self._sessions.append_turn(
                session_id,
                role="system",
                content=f"error: {exc}",
                metadata={"agent": "error"},
            )
            return {
                "type": "error",
                "content": "Lo siento, ocurrió un error al procesar tu mensaje.",
                "status": "failed",
                "session_id": session_id,
                "error": str(exc),
            }

        # Record the assistant turn (persisted to SQLite if memory attached).
        await self._sessions.append_turn(
            session_id,
            role="assistant",
            content=response_text,
            metadata={"agent": agent.name},
        )

        return {
            "type": "message",
            "content": response_text,
            "status": "completed",
            "session_id": session_id,
            "message_id": f"{session_id}:{len(self._sessions.history(session_id))}",
        }

    async def stream_message(
        self,
        message: str,
        session_id: str,
        *,
        mode: str = "confirmation",
        chunk_size: int = 20,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream an assistant response in chunks.

        The response is generated synchronously and then split into
        word-bounded chunks to demonstrate the streaming protocol.
        Sprint 5 will plug in real LLM streaming.

        Yields:
            Envelope dicts. The first chunk has type="chunk" with
            status="streaming"; the final envelope has type="message"
            with status="completed".
        """
        result = await self.handle_message(message, session_id, mode=mode)

        if result["type"] == "error":
            yield result
            return

        full_text: str = result["content"]
        words = full_text.split()
        chunk: list[str] = []
        word_count = 0

        for word in words:
            chunk.append(word)
            word_count += 1
            if len(chunk) >= chunk_size or word_count == len(words):
                yield {
                    "type": "chunk",
                    "content": " ".join(chunk),
                    "status": "streaming",
                    "session_id": session_id,
                }
                chunk = []

        # Final marker so the client can finalize the bubble.
        yield {
            "type": "message",
            "content": full_text,
            "status": "completed",
            "session_id": session_id,
            "message_id": result.get("message_id"),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_context(self, session_id: str) -> dict[str, Any]:
        """Build the agent context for the current session."""
        snapshot = self._sessions.snapshot(session_id) or {}
        return {
            "session_id": session_id,
            "history": snapshot.get("history", []),
            "user_language": snapshot.get("user_language", "es"),
            "mode": snapshot.get("last_mode", "confirmation"),
        }