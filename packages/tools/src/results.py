"""Data models for the Tool Execution Pipeline.

Sprint 4.3 — Tool Execution Pipeline.

Defines all input/output models used during tool execution:

- :class:`ExecutionStatus` — lifecycle states for a pipeline execution.
- :class:`ExecutionSession` — tracks a single execution run from start
  to finish with full metadata.
- :class:`ExecutionResult` — structured output returned to the caller.
- :class:`ToolArguments` — validated, serialisable argument container.
- :class:`ExecutionContext` — runtime context (agent, session, user).
- :class:`ToolSelectionResult` — bridge type carrying the tool chosen
  by the :class:`~src.selection.ToolSelectionEngine` into the pipeline.

All models use ``@dataclass`` for clarity and are frozen where
immutability is appropriate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.base import BaseTool

# ======================================================================
# Execution status
# ======================================================================


class ExecutionStatus(str, Enum):
    """Lifecycle states for a pipeline execution.

    Transitions::

        CREATED → VALIDATING → PREPARING → RUNNING → COMPLETED
                                              → FAILED
                                              → TIMEOUT
                                              → CANCELLED
    """

    CREATED = "created"
    VALIDATING = "validating"
    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Whether this status is a final, non-transitioning state."""
        return self in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.CANCELLED,
        )


# ======================================================================
# Session
# ======================================================================


@dataclass
class ExecutionSession:
    """Tracks a single execution run from start to finish.

    Attributes:
        id: Unique session identifier (short UUID).
        tool_id: The ``tool_id`` of the tool being executed.
        tool_name: Human-readable tool name.
        status: Current lifecycle status.
        started_at: Timestamp when the session was created.
        finished_at: Timestamp when the session reached a terminal
            status, or ``None`` if still in progress.
        duration_ms: Wall-clock duration in milliseconds, or ``None``
            if the session has not finished.
        context: Snapshot of the execution context at creation time.
        error: Error message if the session ended in a failure state.
        events: Ordered list of status-transition events for auditing.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_id: str = ""
    tool_name: str = ""
    status: ExecutionStatus = ExecutionStatus.CREATED
    started_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    finished_at: datetime | None = None
    duration_ms: int | None = None
    context: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, new_status: ExecutionStatus) -> None:
        """Record a status transition event.

        Args:
            new_status: The status to transition to.

        Raises:
            ValueError: If *new_status* is the same as the current
                status or if the current status is already terminal.
        """
        if new_status == self.status:
            raise ValueError(
                f"Cannot transition to the same status '{new_status.value}'"
            )
        if self.status.is_terminal:
            raise ValueError(
                f"Session '{self.id}' is in terminal state "
                f"'{self.status.value}', cannot transition to "
                f"'{new_status.value}'"
            )

        now = datetime.now(timezone.utc)
        event: dict[str, Any] = {
            "from": self.status.value,
            "to": new_status.value,
            "timestamp": now.isoformat(),
        }

        # Terminal transitions get a finished_at and duration.
        if new_status.is_terminal:
            self.finished_at = now
            self.duration_ms = int((now - self.started_at).total_seconds() * 1000)

        self.status = new_status
        self.events.append(event)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the session to a plain dictionary."""
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": (self.finished_at.isoformat() if self.finished_at else None),
            "duration_ms": self.duration_ms,
            "context": self.context,
            "error": self.error,
            "events": self.events,
        }


# ======================================================================
# Result
# ======================================================================


@dataclass
class ExecutionResult:
    """Structured output returned after a pipeline execution.

    Attributes:
        success: Whether the tool executed successfully.
        tool: Reference to the tool that was executed (may be ``None``
            if the tool could not be resolved).
        tool_name: Name of the executed tool.
        output: The tool's return value (if successful).
        error: Error message (if unsuccessful).
        metadata: Additional structured metadata (session id, timing,
            etc.).
        execution_time_ms: Wall-clock execution time in milliseconds.
    """

    success: bool
    tool_name: str = ""
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: int = 0

    @property
    def tool(self) -> BaseTool | None:
        return self.metadata.get("_tool")

    def to_dict(self) -> dict[str, Any]:
        """Serialise the result to a plain dictionary."""
        return {
            "success": self.success,
            "tool_name": self.tool_name,
            "output": self.output,
            "error": self.error,
            "metadata": {k: v for k, v in self.metadata.items() if k != "_tool"},
            "execution_time_ms": self.execution_time_ms,
        }


# ======================================================================
# Arguments
# ======================================================================


@dataclass(frozen=True)
class ToolArguments:
    """Immutable, validated argument container for tool execution.

    Attributes:
        args: The raw keyword arguments dict.
    """

    args: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an argument by name with an optional default."""
        return self.args.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.args

    def __len__(self) -> int:
        return len(self.args)


# ======================================================================
# Context
# ======================================================================


@dataclass(frozen=True)
class ExecutionContext:
    """Runtime context for a tool execution.

    Carries agent identity, session, user, and any other runtime
    information the tool or pipeline may need.

    Attributes:
        session_id: Identifier for the current session.
        agent_name: Name of the executing agent.
        agent_id: Identifier for the executing agent.
        user_id: Identifier for the end user.
        request_id: Identifier for the user request that triggered
            this execution.
        extra: Additional arbitrary context key-value pairs.
    """

    session_id: str = ""
    agent_name: str = ""
    agent_id: str = ""
    user_id: str = ""
    request_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a mutable copy as a plain dictionary.

        This is the format passed into ``BaseTool.execute(context=...)``.
        """
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "request_id": self.request_id,
        }
        d.update(self.extra)
        return d


# ======================================================================
# Selection result bridge
# ======================================================================


@dataclass(frozen=True)
class ToolSelectionResult:
    """Bridge type carrying the tool chosen by the selection engine
    into the execution pipeline.

    The :class:`~src.selection.ToolSelectionEngine` returns a
    :class:`~src.base.BaseTool` directly.  This wrapper carries the
    tool alongside the selection context (score, reason) so the
    pipeline can log and report why the tool was chosen.

    Attributes:
        tool: The selected tool instance.
        score: Ranking score assigned by the selection engine.
        reason: Human-readable explanation for the selection.
        task_name: Name of the task the tool was selected for.
    """

    tool: BaseTool
    score: float = 0.0
    reason: str = ""
    task_name: str = ""


__all__ = [
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionSession",
    "ExecutionStatus",
    "ToolArguments",
    "ToolSelectionResult",
]
