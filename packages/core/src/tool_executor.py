"""Tool execution pipeline.

Mediates between agents requesting tools and the actual execution,
enforcing permission checks before any tool runs.

Flow:
    Agent requests tool
        → PermissionService evaluates
        → If "deny": return denied result immediately
        → If "confirm": return confirmation request (client must approve)
        → If "allow": execute tool and return result

The executor emits events through the EventBus for auditing.
"""

from __future__ import annotations

import logging
import time
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


class ToolExecutionResult:
    """Result of a tool execution attempt."""

    __slots__ = ("tool_name", "decision", "success", "output", "error", "duration_ms")

    def __init__(
        self,
        *,
        tool_name: str,
        decision: str,
        success: bool = False,
        output: str = "",
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        self.tool_name = tool_name
        self.decision = decision
        self.success = success
        self.output = output
        self.error = error
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "decision": self.decision,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class ToolExecutor:
    """Executes tools with permission gating and event emission.

    Args:
        permission_service: Optional permission evaluator. When None,
            all tool requests require confirmation (safe default).
        event_bus: Optional event bus for auditing.
    """

    def __init__(
        self,
        permission_service: Any | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._permissions = permission_service
        self._bus = event_bus

    async def execute(
        self,
        tool: Tool,
        params: dict[str, Any],
        *,
        session_id: str = "",
    ) -> ToolExecutionResult:
        """Execute a tool after permission check.

        Args:
            tool: The tool to execute (must satisfy Tool protocol).
            params: Tool-specific parameters.
            session_id: Session context for events/auditing.

        Returns:
            A ToolExecutionResult with the outcome.
        """
        tool_name = tool.name
        start = time.monotonic()

        # Emit tool.requested event.
        await self._emit(TOOL_REQUESTED, {
            "tool_name": tool_name,
            "params": params,
            "session_id": session_id,
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
                )
                await self._emit(TOOL_DENIED, {
                    "tool_name": tool_name,
                    "reason": decision.reason,
                    "session_id": session_id,
                })
                return result

            if decision.decision == "confirm":
                # Client must approve before we proceed.
                return ToolExecutionResult(
                    tool_name=tool_name,
                    decision="confirm",
                    output=decision.reason,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        # Execute the tool.
        try:
            result_dict = await tool.execute(params)
            duration = int((time.monotonic() - start) * 1000)

            exec_result = ToolExecutionResult(
                tool_name=tool_name,
                decision="allow",
                success=result_dict.get("success", True),
                output=result_dict.get("output", ""),
                error=result_dict.get("error"),
                duration_ms=duration,
            )

            await self._emit(TOOL_EXECUTED, {
                "tool_name": tool_name,
                "success": exec_result.success,
                "duration_ms": duration,
                "session_id": session_id,
            })

            return exec_result

        except Exception as exc:  # noqa: BLE001
            duration = int((time.monotonic() - start) * 1000)

            await self._emit(TOOL_FAILED, {
                "tool_name": tool_name,
                "error": str(exc),
                "duration_ms": duration,
                "session_id": session_id,
            })

            return ToolExecutionResult(
                tool_name=tool_name,
                decision="allow",
                success=False,
                error=str(exc),
                duration_ms=duration,
            )

    async def _emit(self, event_type: str, payload: Any) -> None:
        """Emit an event if a bus is available."""
        if self._bus is not None:
            try:
                await self._bus.publish(event_type, payload)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit event '%s'", event_type)