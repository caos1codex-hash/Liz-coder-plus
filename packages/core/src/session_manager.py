"""Session manager.

Owns the lifetime of a conversation session. A session bundles:

- A stable `session_id` (UUID4 string).
- A short-term conversation history (in-memory list of ChatTurn).
- Per-session user state (currently: language preference and the
  last permission mode the client requested).

Sprint 1 - Prompt 3: integrated with MemoryManager for persistent
storage. When a MemoryManager is attached, every ``append_turn``
also persists the message to SQLite. On ``restore_session``, the
RAM cache is populated from the persistent store.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.events import MEMORY_STORED

if TYPE_CHECKING:
    from src.memory.manager import MemoryManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatTurn:
    """A single turn in the conversation history.

    Fields:
        role:       "user" | "assistant" | "system"
        content:    Raw message text.
        timestamp:  UTC datetime when the turn was recorded.
        metadata:   Optional bag for tags (agent name, mode, etc.).
    """

    role: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionState:
    """Mutable per-session state.

    Fields:
        session_id:        Stable session identifier.
        history:           Ordered list of ChatTurn.
        user_language:     Preferred language code ("es", "en", ...).
        last_mode:         Last permission mode requested by the client.
        created_at:        When the session was first registered.
        last_active_at:    Updated on every inbound message.
    """

    session_id: str
    history: list[ChatTurn] = field(default_factory=list)
    user_language: str = "es"
    last_mode: str = "confirmation"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Update last_active_at to now."""
        self.last_active_at = datetime.now(timezone.utc)


class SessionManager:
    """In-memory store of conversation sessions.

    When a ``MemoryManager`` is attached via ``attach_memory``, every
    turn is also persisted to SQLite. The in-memory RAM cache remains
    the primary read path for performance.

    The manager emits events through the optional EventBus passed in
    the constructor. When no bus is provided (e.g. in unit tests),
    the manager runs silently.
    """

    # Maximum turns kept in-memory per session before older ones are
    # dropped.
    MAX_HISTORY = 200

    def __init__(self, event_bus: Any | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._bus = event_bus
        self._memory: MemoryManager | None = None

    # ------------------------------------------------------------------
    # Memory integration
    # ------------------------------------------------------------------

    def attach_memory(self, memory: MemoryManager) -> None:
        """Attach a persistent memory backend.

        Once attached, ``append_turn`` will also persist messages
        to SQLite via the MemoryManager.

        Args:
            memory: An initialized MemoryManager instance.
        """
        self._memory = memory
        logger.info("Persistent memory attached to SessionManager")

    @property
    def has_memory(self) -> bool:
        """Whether a persistent memory backend is attached."""
        return self._memory is not None

    async def restore_session(self, session_id: str) -> SessionState:
        """Restore a session from persistent memory into RAM.

        Loads the conversation history from SQLite and populates the
        in-memory cache. If the session already exists in RAM, it is
        returned as-is (no reload).

        This is the recommended way to resume a session after a
        process restart.

        Args:
            session_id: The session to restore.

        Returns:
            The SessionState with history loaded from persistent storage.
        """
        if session_id in self._sessions:
            return self._sessions[session_id]

        state = SessionState(session_id=session_id)

        # Load history from persistent memory.
        if self._memory is not None and self._memory.is_initialized:
            try:
                messages = await self._memory.get_context(session_id)
                for msg in messages:
                    turn = ChatTurn(
                        role=msg["role"],
                        content=msg["content"],
                        metadata=msg.get("metadata", {}),
                    )
                    state.history.append(turn)
                logger.info(
                    "Session restored from memory: %s (%d turns)",
                    session_id,
                    len(state.history),
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to restore session %s from memory", session_id
                )

        self._sessions[session_id] = state
        return state

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_session(self, session_id: str | None = None) -> SessionState:
        """Create a new session.

        Args:
            session_id: Optional id. When None, a new UUID4 is generated.

        Returns:
            The freshly created SessionState.
        """
        sid = session_id or _new_uuid()
        if sid in self._sessions:
            logger.warning("Session %s already exists; returning existing", sid)
            return self._sessions[sid]

        state = SessionState(session_id=sid)
        self._sessions[sid] = state
        logger.info("Session created: %s", sid)
        return state

    def get_or_create(self, session_id: str) -> SessionState:
        """Return the session with the given id, creating it if missing."""
        if session_id not in self._sessions:
            return self.create_session(session_id)
        return self._sessions[session_id]

    def get(self, session_id: str) -> SessionState | None:
        """Return the session state, or None if unknown."""
        return self._sessions.get(session_id)

    async def drop(self, session_id: str) -> bool:
        """Drop a session from memory and persistent storage.

        Returns:
            True if a session was removed, False if it didn't exist.
        """
        existed = self._sessions.pop(session_id, None) is not None

        # Also clear from persistent memory.
        if self._memory is not None and self._memory.is_initialized:
            try:
                await self._memory.clear_session(session_id)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to clear session %s from persistent memory",
                    session_id,
                )

        if existed:
            logger.info("Session dropped: %s", session_id)
        return existed

    def list_sessions(self) -> list[str]:
        """List all known session ids."""
        return list(self._sessions.keys())

    @property
    def session_count(self) -> int:
        """Number of sessions currently in memory."""
        return len(self._sessions)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatTurn:
        """Append a turn to the session history.

        If the session doesn't exist yet, it is created on the fly.
        When the history exceeds MAX_HISTORY, the oldest turns are
        dropped.

        If a MemoryManager is attached, the turn is also persisted
        to SQLite.

        Returns:
            The ChatTurn that was appended.
        """
        state = self.get_or_create(session_id)
        turn = ChatTurn(role=role, content=content, metadata=metadata or {})
        state.history.append(turn)

        # Trim if needed (keep the most recent MAX_HISTORY turns).
        if len(state.history) > self.MAX_HISTORY:
            overflow = len(state.history) - self.MAX_HISTORY
            del state.history[:overflow]

        state.touch()

        # Persist to memory backend.
        if self._memory is not None and self._memory.is_initialized:
            try:
                await self._memory.add_message(
                    session_id, role, content, metadata
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to persist turn for session %s", session_id
                )

        if self._bus is not None:
            try:
                # Fire-and-forget: bus.publish is awaitable but we are
                # in an async context now, so we can use ensure_future.
                asyncio.ensure_future(
                    self._bus.publish(
                        MEMORY_STORED,
                        {"session_id": session_id, "turn": turn.__dict__},
                    )
                )
            except RuntimeError:
                # No running loop; ignore (tests may not have one).
                pass

        return turn

    def history(self, session_id: str, limit: int | None = None) -> list[ChatTurn]:
        """Return the session history, most recent last.

        Args:
            session_id: The session to inspect.
            limit:      If provided, only the last `limit` turns are
                        returned.
        """
        state = self._sessions.get(session_id)
        if state is None:
            return []
        if limit is None:
            return list(state.history)
        return list(state.history[-limit:])

    # ------------------------------------------------------------------
    # User state
    # ------------------------------------------------------------------

    def set_language(self, session_id: str, language: str) -> None:
        """Persist the user's preferred language for this session."""
        state = self.get_or_create(session_id)
        state.user_language = language
        state.touch()

    def set_mode(self, session_id: str, mode: str) -> None:
        """Persist the last permission mode requested by the client."""
        state = self.get_or_create(session_id)
        state.last_mode = mode
        state.touch()

    def snapshot(self, session_id: str) -> dict[str, Any] | None:
        """Return a JSON-serializable snapshot of the session state.

        Used to build conversation context for agents.
        """
        state = self._sessions.get(session_id)
        if state is None:
            return None
        return {
            "session_id": state.session_id,
            "history": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": t.timestamp.isoformat(),
                    "metadata": t.metadata,
                }
                for t in state.history
            ],
            "user_language": state.user_language,
            "last_mode": state.last_mode,
            "created_at": state.created_at.isoformat(),
            "last_active_at": state.last_active_at.isoformat(),
        }


def _new_uuid() -> str:
    """Generate a fresh UUID4 string."""
    from uuid import uuid4

    return str(uuid4())