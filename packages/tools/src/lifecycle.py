"""Execution Lifecycle Manager.

Sprint 4.3 — Tool Execution Pipeline.

Provides :class:`ExecutionLifecycleManager` which is responsible for
creating, updating, and closing :class:`~src.results.ExecutionSession`
instances.  It acts as a thin event-sink: every status transition is
recorded as an event and emitted via Python's ``logging`` module.

The manager does **not** execute tools — it only tracks state.
"""

from __future__ import annotations

import logging
from typing import Any

from src.base import BaseTool
from src.results import ExecutionContext, ExecutionSession, ExecutionStatus

logger = logging.getLogger(__name__)


class ExecutionLifecycleManager:
    """Creates, transitions, and closes execution sessions.

    Usage::

        mgr = ExecutionLifecycleManager()
        session = mgr.create_session(tool, exec_ctx)
        mgr.transition(session, ExecutionStatus.VALIDATING)
        # ... later ...
        mgr.transition(session, ExecutionStatus.COMPLETED)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ExecutionSession] = {}

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    def create_session(
        self,
        tool: BaseTool,
        exec_ctx: ExecutionContext,
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> ExecutionSession:
        """Create a new session in ``CREATED`` status.

        Args:
            tool: The tool that will be executed.
            exec_ctx: Runtime execution context.
            extra_context: Additional key-value pairs merged into
                the session's context snapshot.

        Returns:
            A fresh :class:`ExecutionSession`.

        Raises:
            RuntimeError: If a session with the same tool_id already
                exists and is not in a terminal state.
        """
        # Guard against overlapping sessions for the same tool.
        active = [
            s
            for s in self._sessions.values()
            if s.tool_id == tool.tool_id and not s.status.is_terminal
        ]
        if active:
            raise RuntimeError(
                f"Tool '{tool.name}' (id={tool.tool_id}) already has "
                f"an active session (id={active[0].id}, "
                f"status={active[0].status.value})"
            )

        ctx_snapshot: dict[str, Any] = exec_ctx.to_dict()
        if extra_context:
            ctx_snapshot.update(extra_context)

        session = ExecutionSession(
            tool_id=tool.tool_id,
            tool_name=tool.name,
            context=ctx_snapshot,
        )
        self._sessions[session.id] = session

        logger.info(
            "Session '%s' created for tool '%s' (id=%s)",
            session.id,
            tool.name,
            tool.tool_id,
        )
        return session

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        session: ExecutionSession,
        new_status: ExecutionStatus,
    ) -> None:
        """Transition a session to a new status.

        Delegates to :meth:`ExecutionSession.transition` and logs
        the event.

        Args:
            session: The session to transition.
            new_status: Target status.

        Raises:
            ValueError: If the transition is invalid.
            KeyError: If the session is not tracked by this manager.
        """
        if session.id not in self._sessions:
            raise KeyError(
                f"Session '{session.id}' is not managed by this " f"lifecycle manager"
            )

        old_status = session.status
        session.transition(new_status)

        log_fn = logger.info if new_status.is_terminal else logger.debug
        log_fn(
            "Session '%s': %s → %s (tool='%s')",
            session.id,
            old_status.value,
            new_status.value,
            session.tool_name,
        )

        # If terminal, capture error from the last event if present.
        if new_status.is_terminal and session.error is None:
            if new_status in (
                ExecutionStatus.FAILED,
                ExecutionStatus.TIMEOUT,
                ExecutionStatus.CANCELLED,
            ):
                # Error will be set by the pipeline; nothing to do here.
                pass

    # ------------------------------------------------------------------
    # Error recording
    # ------------------------------------------------------------------

    def set_error(
        self,
        session: ExecutionSession,
        error_message: str,
    ) -> None:
        """Record an error message on the session.

        This is typically called before transitioning the session
        to a terminal failure state.

        Args:
            session: The session to annotate.
            error_message: Human-readable error description.
        """
        session.error = error_message
        logger.error(
            "Session '%s' error: %s (tool='%s')",
            session.id,
            error_message,
            session.tool_name,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> ExecutionSession | None:
        """Retrieve a session by its identifier.

        Returns:
            The session, or ``None`` if not found.
        """
        return self._sessions.get(session_id)

    def active_sessions(self) -> list[ExecutionSession]:
        """Return all sessions that are not in a terminal state."""
        return [s for s in self._sessions.values() if not s.status.is_terminal]

    def sessions_for_tool(self, tool_id: str) -> list[ExecutionSession]:
        """Return all sessions for a given tool, newest first."""
        sessions = [s for s in self._sessions.values() if s.tool_id == tool_id]
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions

    def all_sessions(self) -> list[ExecutionSession]:
        """Return all tracked sessions, newest first."""
        sessions = list(self._sessions.values())
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions

    def summary(self) -> dict[str, Any]:
        """Return a summary of all tracked sessions."""
        total = len(self._sessions)
        active = len(self.active_sessions())
        by_status: dict[str, int] = {}
        for s in self._sessions.values():
            key = s.status.value
            by_status[key] = by_status.get(key, 0) + 1
        return {
            "total": total,
            "active": active,
            "completed": total - active,
            "by_status": by_status,
        }


__all__ = [
    "ExecutionLifecycleManager",
]
