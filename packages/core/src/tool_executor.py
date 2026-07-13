"""Tool execution pipeline.

Mediates between agents requesting tools and the actual execution,
enforcing permission checks before any tool runs.

Flow:
    Agent requests tool
        → PermissionService evaluates
        → If "deny": return denied result immediately
        → If "confirm": return confirmation request (client must approve)
        → If "allow": execute tool with timeout
            → On timeout: cancel and return timeout error
            → On error: capture, log, return structured error
            → On success: return structured result

The executor emits events through the EventBus for auditing.
Supports timeout, cancellation, execution context, and agent tracking.

Sprint 1.6 — Added timeout, cancellation, context passing, agent
tracking, structured error handling, and action logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from src.events import (
    TOOL_DENIED,
    TOOL_EXECUTED,
    TOOL_FAILED,
    TOOL_REQUESTED,
)

if TYPE_CHECKING:
    from src.event_bus import EventBus
    from src.protocols import Tool

logger = logging.getLogger(__name__)

# Default timeout for tool execution (seconds).
DEFAULT_TOOL_TIMEOUT = 30.0


class ToolExecutionResult:
    """Result of a tool execution attempt.

    Attributes:
        tool_name: Name of the tool that was executed.
        decision: Permission decision ("allow", "confirm", "deny", "timeout").
        success: Whether the tool executed successfully.
        output: Tool output string (if any).
        error: Error message (if any).
        duration_ms: Execution time in milliseconds.
        agent_name: Name of the agent that requested the tool.
        session_id: Session in which the tool was executed.
        tool_id: Unique id of the tool instance (if available).
        execution_id: Unique execution id for cancellation.
    """

    __slots__ = (
        "tool_name",
        "decision",
        "success",
        "output",
        "error",
        "duration_ms",
        "agent_name",
        "session_id",
        "tool_id",
        "execution_id",
    )

    def __init__(
        self,
        *,
        tool_name: str,
        decision: str,
        success: bool = False,
        output: str = "",
        error: str | None = None,
        duration_ms: int = 0,
        agent_name: str = "",
        session_id: str = "",
        tool_id: str = "",
        execution_id: str = "",
    ) -> None:
        self.tool_name = tool_name
        self.decision = decision
        self.success = success
        self.output = output
        self.error = error
        self.duration_ms = duration_ms
        self.agent_name = agent_name
        self.session_id = session_id
        self.tool_id = tool_id
        self.execution_id = execution_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "decision": self.decision,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "tool_id": self.tool_id,
            "execution_id": self.execution_id,
        }


class ToolExecutor:
    """Executes tools with permission gating, timeout, and event emission.

    Every execution follows the pipeline:
        1. Emit tool.requested event.
        2. Permission check (deny/confirm/allow).
        3. Execute with timeout (if allowed).
        4. Emit tool.executed or tool.failed event.

    Args:
        permission_service: Optional permission evaluator. When None,
            all tool requests are executed directly (tools with
            HIGH permission_level should still be confirmed by the
            PermissionService).
        event_bus: Optional event bus for auditing.
        default_timeout: Default timeout in seconds for tool execution.
            Individual tools may override via ``timeout`` kwarg.
    """

    def __init__(
        self,
        permission_service: Any | None = None,
        event_bus: EventBus | None = None,
        *,
        default_timeout: float = DEFAULT_TOOL_TIMEOUT,
    ) -> None:
        self._permissions = permission_service
        self._bus = event_bus
        self._default_timeout = default_timeout
        # Track active tasks keyed by unique execution_id for
        # cancellation of concurrent executions of the same tool.
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    async def execute(
        self,
        tool: Tool,
        params: dict[str, Any],
        *,
        session_id: str = "",
        agent_name: str = "",
        timeout: float | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool after permission check.

        Args:
            tool: The tool to execute (must satisfy Tool protocol).
            params: Tool-specific parameters.
            session_id: Session context for events/auditing.
            agent_name: Name of the requesting agent.
            timeout: Override the default timeout for this execution.

        Returns:
            A ToolExecutionResult with the outcome. The ``execution_id``
            field can be used to cancel this specific execution.
        """
        tool_name = tool.name
        execution_id = str(uuid.uuid4())
        effective_timeout = timeout if timeout is not None else self._default_timeout
        tool_id = getattr(tool, "tool_id", "")
        start = time.monotonic()

        # Build context for tools that accept it.
        context: dict[str, Any] = {
            "session_id": session_id,
            "agent_name": agent_name,
        }

        # Emit tool.requested event.
        await self._emit(TOOL_REQUESTED, {
            "tool_name": tool_name,
            "tool_id": tool_id,
            "params": params,
            "session_id": session_id,
            "agent_name": agent_name,
        })

        # Permission check.
        if self._permissions is not None:
            decision = self._permissions.evaluate(tool_name)
            if decision.decision == "deny":
                duration = int((time.monotonic() - start) * 1000)
                result = ToolExecutionResult(
                    tool_name=tool_name,
                    decision="deny",
                    error=decision.reason,
                    duration_ms=duration,
                    agent_name=agent_name,
                    session_id=session_id,
                    tool_id=tool_id,
                )
                await self._emit(TOOL_DENIED, {
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "reason": decision.reason,
                    "session_id": session_id,
                    "agent_name": agent_name,
                })
                return result

            if decision.decision == "confirm":
                # Client must approve before we proceed.
                return ToolExecutionResult(
                    tool_name=tool_name,
                    decision="confirm",
                    output=decision.reason,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    agent_name=agent_name,
                    session_id=session_id,
                    tool_id=tool_id,
                )

        # Execute the tool with timeout.
        task: asyncio.Task[dict[str, Any]] | None = None
        try:
            # Determine call signature — BaseTool subclasses accept context.
            import inspect
            sig = inspect.signature(tool.execute)
            if "context" in sig.parameters:
                coro = tool.execute(params, context=context)
            else:
                coro = tool.execute(params)

            task = asyncio.create_task(coro)
            self._active_tasks[execution_id] = task

            result_dict = await asyncio.wait_for(
                task, timeout=effective_timeout
            )
            duration = int((time.monotonic() - start) * 1000)

            exec_result = ToolExecutionResult(
                tool_name=tool_name,
                decision="allow",
                success=result_dict.get("success", True),
                output=str(result_dict.get("output", "")),
                error=result_dict.get("error"),
                duration_ms=duration,
                agent_name=agent_name,
                session_id=session_id,
                tool_id=getattr(result_dict, "tool_id", "") or tool_id,
                execution_id=execution_id,
            )

            await self._emit(TOOL_EXECUTED, {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "success": exec_result.success,
                "duration_ms": duration,
                "session_id": session_id,
                "agent_name": agent_name,
            })

            return exec_result

        except asyncio.TimeoutError:
            duration = int((time.monotonic() - start) * 1000)
            error_msg = (
                f"Tool '{tool_name}' timed out after "
                f"{effective_timeout:.1f}s"
            )

            # Cancel the task if still pending.
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            await self._emit(TOOL_FAILED, {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "error": error_msg,
                "error_type": "timeout",
                "duration_ms": duration,
                "session_id": session_id,
                "agent_name": agent_name,
            })

            logger.warning(error_msg)
            return ToolExecutionResult(
                tool_name=tool_name,
                decision="timeout",
                success=False,
                error=error_msg,
                duration_ms=duration,
                agent_name=agent_name,
                session_id=session_id,
                tool_id=tool_id,
                execution_id=execution_id,
            )

        except asyncio.CancelledError:
            duration = int((time.monotonic() - start) * 1000)
            error_msg = f"Tool '{tool_name}' was cancelled"

            await self._emit(TOOL_FAILED, {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "error": error_msg,
                "error_type": "cancelled",
                "duration_ms": duration,
                "session_id": session_id,
                "agent_name": agent_name,
            })

            logger.info(error_msg)
            return ToolExecutionResult(
                tool_name=tool_name,
                decision="cancelled",
                success=False,
                error=error_msg,
                duration_ms=duration,
                agent_name=agent_name,
                session_id=session_id,
                tool_id=tool_id,
                execution_id=execution_id,
            )

        except Exception as exc:  # noqa: BLE001
            duration = int((time.monotonic() - start) * 1000)
            error_str = str(exc)

            await self._emit(TOOL_FAILED, {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "error": error_str,
                "error_type": type(exc).__name__,
                "duration_ms": duration,
                "session_id": session_id,
                "agent_name": agent_name,
            })

            logger.exception(
                "Tool '%s' failed: %s", tool_name, error_str
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                decision="allow",
                success=False,
                error=error_str,
                duration_ms=duration,
                agent_name=agent_name,
                session_id=session_id,
                tool_id=tool_id,
                execution_id=execution_id,
            )

        finally:
            self._active_tasks.pop(execution_id, None)

    async def cancel(self, identifier: str) -> bool:
        """Cancel a running tool execution.

        Accepts either an ``execution_id`` (preferred) or a ``tool_name``
        for backward compatibility. When a ``tool_name`` is provided and
        multiple executions of that tool are active, the most recent one
        is cancelled.

        Args:
            identifier: The execution_id or tool_name to cancel.

        Returns:
            True if a task was found and cancelled, False otherwise.
        """
        # Try execution_id first.
        task = self._active_tasks.get(identifier)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled execution %s", identifier)
            return True

        # Backward compat: try tool_name (cancel most recent match).
        for eid, t in list(self._active_tasks.items()):
            if not t.done():
                t.cancel()
                logger.info(
                    "Cancelled execution %s (matched tool_name '%s')",
                    eid, identifier,
                )
                return True
        return False

    async def cancel_by_id(self, execution_id: str) -> bool:
        """Cancel a running tool execution by its unique execution ID.

        Args:
            execution_id: The unique ID returned in ``ToolExecutionResult``.

        Returns:
            True if the task was found and cancelled, False otherwise.
        """
        task = self._active_tasks.get(execution_id)
        if task is not None and not task.done():
            task.cancel()
            logger.info("Cancelled execution %s", execution_id)
            return True
        return False

    async def cancel_all(self) -> int:
        """Cancel all running tool executions.

        Returns:
            The number of tasks cancelled.
        """
        cancelled = 0
        for eid, task in list(self._active_tasks.items()):
            if not task.done():
                task.cancel()
                cancelled += 1
                logger.info("Cancelled execution %s", eid)
        return cancelled

    @property
    def active_count(self) -> int:
        """Number of currently executing tools."""
        return len(self._active_tasks)

    async def _emit(self, event_type: str, payload: Any) -> None:
        """Emit an event if a bus is available."""
        if self._bus is not None:
            try:
                await self._bus.publish(event_type, payload)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit event '%s'", event_type)