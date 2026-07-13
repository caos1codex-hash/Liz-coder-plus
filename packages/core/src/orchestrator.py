"""Core orchestrator.

The orchestrator is the central hub of Liz Coder Plus. It receives user
input from the API layer, decides which agent to invoke (via the
AgentRouter), coordinates session/memory access, dispatches tools, and
emits events.

Sprint 1 - Prompt 3: connected to the persistent memory system.
Sprint 1.5: agent validation, execution timeout, EventBus integration,
ToolExecutor for permission-gated tool execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

from src.agent_router import AgentRouter, EchoAgent
from src.event_bus import EventBus
from src.events import (
    AGENT_COMPLETED,
    AGENT_FAILED,
    AGENT_INVOKED,
    SYSTEM_STARTED,
    SYSTEM_STOPPED,
    TOOL_REQUESTED,
    USER_MESSAGE_RECEIVED,
)
from src.protocols import Agent, Tool
from src.session_manager import SessionManager
from src.tool_executor import ToolExecutor
from src.ws_manager import WebSocketManager

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager

    # Lazy import to avoid hard dependency on agents package.
    # BaseAgent lifecycle is used when available.
    from src.base import BaseAgent as BaseAgentClass

logger = logging.getLogger(__name__)

# Default timeout for agent execution (seconds).
DEFAULT_AGENT_TIMEOUT = 30.0


class Orchestrator:
    """Central coordinator for agents, memory, sessions and tools.

    Wiring:
      - WebSocketManager  -> tracks live connections
      - SessionManager    -> tracks conversation history per session
      - AgentRouter       -> selects an agent per message
      - EventBus          -> publishes lifecycle events
      - ToolExecutor      -> executes tools with permission gating
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
        event_bus: EventBus | None = None,
        tool_executor: ToolExecutor | None = None,
        agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    ) -> None:
        self._ws_manager = ws_manager or WebSocketManager()
        self._sessions = session_manager or SessionManager()
        self._router = router or AgentRouter()
        self._bus = event_bus or EventBus()
        self._tool_executor = tool_executor or ToolExecutor(event_bus=self._bus)
        self._agents: dict[str, Agent] = {}
        self._tools: dict[str, Tool] = {}
        self._memory: MemoryManager | None = None
        self._agent_timeout = agent_timeout

        # Make sure the router's default agent is also registered here.
        default = self._router.default_agent
        self._agents[default.name] = default

        self._initialized = False
        self._shutdown = False

        logger.info(
            "Orchestrator initialized (ws=%d sessions=%d agents=%d "
            "timeout=%.1fs bus=%d)",
            self._ws_manager.connection_count,
            self._sessions.session_count,
            len(self._agents),
            self._agent_timeout,
            len(self._bus.event_types()),
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
    def event_bus(self) -> EventBus:
        return self._bus

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._tool_executor

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
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize all registered agents and the orchestrator.

        Transitions the orchestrator from CREATED to ACTIVE state.
        Agents that have an ``initialize()`` method (BaseAgent subclasses)
        are initialized. Errors in individual agents are logged but do
        not prevent other agents from initializing.

        Emits ``system.started`` event.
        """
        if self._initialized:
            logger.warning("Orchestrator already initialized")
            return

        initialized_count = 0
        for name, agent in self._agents.items():
            if hasattr(agent, "initialize") and callable(agent.initialize):
                try:
                    await agent.initialize()
                    initialized_count += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to initialize agent '%s'", name
                    )

        self._initialized = True
        await self._emit(SYSTEM_STARTED, {
            "agents": list(self._agents.keys()),
            "agents_initialized": initialized_count,
            "tools": list(self._tools.keys()),
        })
        logger.info(
            "Orchestrator started (%d/%d agents initialized)",
            initialized_count,
            len(self._agents),
        )

    async def shutdown(self) -> None:
        """Shut down the orchestrator and all agents.

        Safe to call multiple times. Shuts down all agents that
        have a ``shutdown()`` method (BaseAgent subclasses).

        Emits ``system.stopped`` event.
        """
        if self._shutdown:
            return

        shutdown_count = 0
        for name, agent in self._agents.items():
            if hasattr(agent, "shutdown") and callable(agent.shutdown):
                try:
                    await agent.shutdown()
                    shutdown_count += 1
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to shut down agent '%s'", name
                    )

        self._shutdown = True
        await self._emit(SYSTEM_STOPPED, {
            "agents_shutdown": shutdown_count,
        })
        logger.info(
            "Orchestrator stopped (%d agents shut down)",
            shutdown_count,
        )

    @property
    def is_initialized(self) -> bool:
        """Whether the orchestrator has been initialized."""
        return self._initialized

    @property
    def is_shutdown(self) -> bool:
        """Whether the orchestrator has been shut down."""
        return self._shutdown

    def agent_status(self) -> dict[str, dict[str, Any]]:
        """Return status info for all registered agents.

        For BaseAgent subclasses, returns their full metadata.
        For plain Protocol-satisfying objects, returns basic info.
        """
        result = {}
        for name, agent in self._agents.items():
            if hasattr(agent, "metadata"):
                result[name] = agent.metadata
            else:
                result[name] = {"name": name, "type": type(agent).__name__}
        return result

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    async def _emit(self, event_type: str, payload: Any = None) -> None:
        """Emit an event to the bus, isolating failures."""
        try:
            await self._bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit event '%s'", event_type)

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
            1. Emit user.message.received event.
            2. Persist user state and the inbound turn.
            3. Build context from session history.
            4. Route to agent (emit agent.invoked).
            5. Execute agent with timeout.
            6. Emit agent.completed or agent.failed.
            7. Record assistant turn.
            8. Return response envelope.

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

        # Emit user message received event.
        await self._emit(USER_MESSAGE_RECEIVED, {
            "session_id": session_id,
            "message_length": len(message),
            "mode": mode,
        })

        # Persist user state and the inbound turn.
        self._sessions.set_mode(session_id, mode)
        await self._sessions.append_turn(
            session_id, role="user", content=message, metadata={"mode": mode}
        )

        context = self._build_context(session_id)

        try:
            agent = await self._router.route(message, context)
            agent_name = agent.name

            # Emit agent.invoked event.
            await self._emit(AGENT_INVOKED, {
                "agent_name": agent_name,
                "session_id": session_id,
                "message_length": len(message),
            })

            # Execute with timeout to prevent indefinite blocking.
            start = time.monotonic()
            response_text = await asyncio.wait_for(
                agent.handle(message, context),
                timeout=self._agent_timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            # Emit agent.completed event.
            await self._emit(AGENT_COMPLETED, {
                "agent_name": agent_name,
                "session_id": session_id,
                "duration_ms": duration_ms,
                "response_length": len(response_text),
            })

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

            await self._emit(AGENT_FAILED, {
                "agent_name": "unknown",
                "session_id": session_id,
                "error": "timeout",
                "duration_ms": int(self._agent_timeout * 1000),
            })

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

            await self._emit(AGENT_FAILED, {
                "agent_name": "unknown",
                "session_id": session_id,
                "error_type": "validation",
                "error": str(exc),
            })

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

            await self._emit(AGENT_FAILED, {
                "agent_name": "unknown",
                "session_id": session_id,
                "error_type": "unexpected",
                "error": str(exc),
            })

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

    async def execute_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
    ) -> dict[str, Any]:
        """Execute a registered tool with permission gating.

        Args:
            tool_name: Name of the registered tool.
            params: Tool-specific parameters.
            session_id: Session context for events/auditing.

        Returns:
            A dict with the ToolExecutionResult data plus tool_name.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return {
                "tool_name": tool_name,
                "decision": "deny",
                "success": False,
                "error": f"Tool '{tool_name}' is not registered.",
                "duration_ms": 0,
            }

        result = await self._tool_executor.execute(
            tool, params, session_id=session_id
        )
        result_dict = result.to_dict()
        return result_dict

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