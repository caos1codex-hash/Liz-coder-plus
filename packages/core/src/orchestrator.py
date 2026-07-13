"""Core orchestrator.

The orchestrator is the central hub of Liz Coder Plus. It receives user
input from the API layer, decides which agent to invoke (via the
AgentRouter), coordinates session/memory access, dispatches tools, and
emits events.

Sprint 1 - Prompt 3: connected to the persistent memory system.
Sprint 1.5: agent validation, execution timeout, improved error handling,
and lifecycle integration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from src.agent_router import AgentRouter, EchoAgent
from src.protocols import Agent, Memory, Tool
from src.session_manager import SessionManager
from src.ws_manager import WebSocketManager

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

# Default timeout for agent execution (seconds).
DEFAULT_AGENT_TIMEOUT = 30.0


class Orchestrator:
    """Central coordinator for agents, memory, sessions and tools.

    Wiring:
      - WebSocketManager  -> tracks live connections
      - SessionManager    -> tracks conversation history per session
      - AgentRouter       -> selects an agent per message
      - MemoryManager     -> persists messages to SQLite (optional)

    Agents are validated against the ``Agent`` Protocol at registration.
    Agent execution is subject to a configurable timeout to prevent
    indefinite blocking.
    """

    def __init__(
        self,
        *,
        ws_manager: WebSocketManager | None = None,
        session_manager: SessionManager | None = None,
        router: AgentRouter | None = None,
        agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    ) -> None:
        self._ws_manager = ws_manager or WebSocketManager()
        self._sessions = session_manager or SessionManager()
        self._router = router or AgentRouter()
        self._agents: dict[str, Agent] = {}
        self._tools: dict[str, Tool] = {}
        self._memory: MemoryManager | None = None
        self._agent_timeout = agent_timeout

        # Make sure the router's default agent is also registered here.
        default = self._router.default_agent
        self._agents[default.name] = default

        logger.info(
            "Orchestrator initialized (ws=%d sessions=%d agents=%d timeout=%.1fs)",
            self._ws_manager.connection_count,
            self._sessions.session_count,
            len(self._agents),
            self._agent_timeout,
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

    @property
    def agent_timeout(self) -> float:
        """Timeout in seconds for agent execution."""
        return self._agent_timeout

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(self, agent: Agent) -> None:
        """Register an agent with both the orchestrator and the router.

        Args:
            agent: Must satisfy the Agent protocol (name + async handle).

        Raises:
            TypeError: If the agent does not satisfy the Agent protocol.
        """
        if not isinstance(agent, Agent):
            raise TypeError(
                f"Object {agent!r} does not satisfy the Agent protocol "
                f"(needs 'name: str' and 'async handle(message, context) -> str')"
            )
        self._agents[agent.name] = agent
        self._router.register_agent(agent)
        logger.debug("Registered agent '%s'", agent.name)

    def register_tool(self, tool: Tool) -> None:
        """Register a tool with the orchestrator.

        Args:
            tool: Must satisfy the Tool protocol (name + async execute).

        Raises:
            TypeError: If the tool does not satisfy the Tool protocol.
        """
        if not isinstance(tool, Tool):
            raise TypeError(
                f"Object {tool!r} does not satisfy the Tool protocol "
                f"(needs 'name: str' and 'async execute(params) -> dict')"
            )
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
            4. Execute agent with timeout.
            5. Record assistant turn.
            6. Return response envelope.

        When a MemoryManager is attached, steps 1 and 5 also persist
        to SQLite via the SessionManager.

        Args:
            message:    Raw user input.
            session_id: Conversation session identifier.
            mode:       Permission mode requested by the client.

        Returns:
            A dict with keys: type, content, status, session_id,
            message_id, agent_name, duration_ms.
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
            agent_name = agent.name

            # Execute with timeout to prevent indefinite blocking.
            import time
            start = time.monotonic()
            response_text = await asyncio.wait_for(
                agent.handle(message, context),
                timeout=self._agent_timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            logger.info(
                "Agent '%s' completed in %dms for session %s",
                agent_name,
                duration_ms,
                session_id,
            )

        except asyncio.TimeoutError:
            logger.error(
                "Agent timed out after %.1fs for session %s",
                self._agent_timeout,
                session_id,
            )
            await self._sessions.append_turn(
                session_id,
                role="system",
                content=f"timeout: agent exceeded {self._agent_timeout}s",
                metadata={"agent": "timeout", "error_type": "timeout"},
            )
            return {
                "type": "error",
                "content": (
                    f"Lo siento, el agente tardó demasiado en responder "
                    f"(límite: {self._agent_timeout}s)."
                ),
                "status": "failed",
                "session_id": session_id,
                "error": "agent_timeout",
                "duration_ms": int(self._agent_timeout * 1000),
            }
        except (ValueError, TypeError) as exc:
            # Validation / type errors from the agent.
            logger.warning("Agent validation error: %s", exc)
            await self._sessions.append_turn(
                session_id,
                role="system",
                content=f"validation_error: {exc}",
                metadata={"agent": "error", "error_type": "validation"},
            )
            return {
                "type": "error",
                "content": "El mensaje no pudo ser procesado correctamente.",
                "status": "failed",
                "session_id": session_id,
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            # Unexpected errors from agents or routing.
            logger.exception("Agent failed to handle message")
            await self._sessions.append_turn(
                session_id,
                role="system",
                content=f"error: {exc}",
                metadata={"agent": "error", "error_type": "unexpected"},
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
            metadata={"agent": agent_name},
        )

        return {
            "type": "message",
            "content": response_text,
            "status": "completed",
            "session_id": session_id,
            "message_id": f"{session_id}:{len(self._sessions.history(session_id))}",
            "agent_name": agent_name,
            "duration_ms": duration_ms,
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
            "agent_name": result.get("agent_name"),
            "duration_ms": result.get("duration_ms", 0),
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