"""Tool Executor — low-level execution runner.

Sprint 4.3 — Tool Execution Pipeline.

Provides :class:`ToolExecutor` which is responsible for **actually
running** a tool's ``execute()`` coroutine.  It handles:

- Sync wrapper around the tool's async ``execute()`` method.
- Configurable and per-tool timeouts via ``asyncio.wait_for``.
- Cancellation via an internal ``asyncio.Event``.
- Exception capture and normalisation.

The executor does **not** validate, select, or track sessions — it
only runs the tool and returns the raw result dict.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.base import BaseTool
from src.results import ToolArguments
from src.validation import ToolExecutionValidator

logger = logging.getLogger(__name__)

# Global default timeout (30 seconds).
DEFAULT_TIMEOUT_MS: int = 30_000


class ToolExecutor:
    """Runs a :class:`BaseTool` with timeout and cancellation support.

    Args:
        default_timeout_ms: Default timeout in milliseconds for tools
            that do not declare their own.
        validator: Optional :class:`ToolExecutionValidator` used for
            pre-execution checks (the pipeline normally handles this
            separately, but the executor can also perform a quick
            check when invoked directly).

    Usage::

        executor = ToolExecutor(default_timeout_ms=10_000)
        result = await executor.run_async(
            tool, arguments, context, timeout_ms=5_000,
        )
    """

    def __init__(
        self,
        *,
        default_timeout_ms: int = DEFAULT_TIMEOUT_MS,
        validator: ToolExecutionValidator | None = None,
    ) -> None:
        self._default_timeout_ms = default_timeout_ms
        self._validator = validator
        self._cancel_events: dict[str, asyncio.Event] = {}

    @property
    def default_timeout_ms(self) -> int:
        return self._default_timeout_ms

    # ------------------------------------------------------------------
    # Async execution
    # ------------------------------------------------------------------

    async def run_async(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
        context: dict[str, Any],
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Execute a tool asynchronously with timeout and cancellation.

        Args:
            tool: The tool to execute.
            arguments: Validated arguments.
            context: Execution context dict.
            timeout_ms: Override timeout in milliseconds. ``None``
                uses the default or the tool's declared timeout.

        Returns:
            The tool's result dict (must contain ``"success"``).

        Raises:
            asyncio.TimeoutError: If the execution exceeds the timeout.
            asyncio.CancelledError: If the execution is cancelled.
            Exception: Any exception raised by the tool itself.
        """
        effective_timeout = self._resolve_timeout(tool, timeout_ms)
        cancel_event = self._get_cancel_event(tool.tool_id)
        context = dict(context)  # shallow copy to avoid mutation.

        logger.debug(
            "Executing tool '%s' (id=%s) with timeout=%dms",
            tool.name,
            tool.tool_id,
            effective_timeout,
        )

        # Optional pre-execution validation.
        if self._validator is not None:
            from src.results import ExecutionContext

            exec_ctx = ExecutionContext(
                **{
                    k: v
                    for k, v in context.items()
                    if k in ExecutionContext.__dataclass_fields__
                },
            )
            self._validator.validate(tool, arguments, exec_ctx)

        start = time.monotonic()

        try:
            result = await asyncio.wait_for(
                self._execute_with_cancel(tool, arguments.args, context, cancel_event),
                timeout=effective_timeout / 1000.0,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.warning(
                "Tool '%s' (id=%s) timed out after %dms " "(limit=%dms)",
                tool.name,
                tool.tool_id,
                elapsed,
                effective_timeout,
            )
            raise
        except asyncio.CancelledError:
            elapsed = int((time.monotonic() - start) * 1000)
            logger.info(
                "Tool '%s' (id=%s) cancelled after %dms",
                tool.name,
                tool.tool_id,
                elapsed,
            )
            raise
        finally:
            self._cleanup_cancel_event(tool.tool_id)

        elapsed = int((time.monotonic() - start) * 1000)
        logger.debug(
            "Tool '%s' (id=%s) finished in %dms (success=%s)",
            tool.name,
            tool.tool_id,
            elapsed,
            result.get("success"),
        )

        return result

    # ------------------------------------------------------------------
    # Sync execution (convenience wrapper)
    # ------------------------------------------------------------------

    def run(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
        context: dict[str, Any],
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Execute a tool synchronously by running the async method
        in a new event loop (or the current running loop).

        In an already-running async context, prefer :meth:`run_async`.

        Args:
            tool: The tool to execute.
            arguments: Validated arguments.
            context: Execution context dict.
            timeout_ms: Override timeout in milliseconds.

        Returns:
            The tool's result dict.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # We're inside an existing event loop — create a new
            # thread with its own loop to avoid deadlock.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
            ) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.run_async(
                        tool,
                        arguments,
                        context,
                        timeout_ms=timeout_ms,
                    ),
                )
                return future.result()

        return asyncio.run(
            self.run_async(
                tool,
                arguments,
                context,
                timeout_ms=timeout_ms,
            ),
        )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, tool_id: str) -> bool:
        """Signal cancellation for a running tool execution.

        This sets an internal flag that the executor checks between
        coroutine steps.  The actual ``CancelledError`` is raised on
        the next await point inside the tool.

        Args:
            tool_id: The ``tool_id`` of the tool to cancel.

        Returns:
            ``True`` if a cancellation signal was sent, ``False`` if
            no active execution was found for the given tool_id.
        """
        event = self._cancel_events.get(tool_id)
        if event is None or event.is_set():
            return False
        event.set()
        logger.info("Cancel signal sent for tool_id=%s", tool_id)
        return True

    def cancel_all(self) -> int:
        """Cancel all currently tracked executions.

        Returns:
            The number of executions that were signalled.
        """
        count = 0
        for tool_id, event in list(self._cancel_events.items()):
            if not event.is_set():
                event.set()
                count += 1
        if count:
            logger.info("Cancel signal sent for %d execution(s)", count)
        return count

    # ------------------------------------------------------------------
    # Timeout resolution
    # ------------------------------------------------------------------

    def timeout(self, tool: BaseTool) -> int:
        """Return the effective timeout in milliseconds for a tool.

        Resolution order:
        1. Tool metadata ``"timeout_ms"``.
        2. Executor's ``default_timeout_ms``.
        """
        return self._resolve_timeout(tool, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_timeout(
        self,
        tool: BaseTool,
        explicit_timeout: int | None,
    ) -> int:
        """Determine the effective timeout for a tool execution."""
        if explicit_timeout is not None:
            return explicit_timeout
        tool_timeout = tool.metadata.get("timeout_ms")
        if tool_timeout is not None:
            return int(tool_timeout)
        return self._default_timeout_ms

    async def _execute_with_cancel(
        self,
        tool: BaseTool,
        params: dict[str, Any],
        context: dict[str, Any],
        cancel_event: asyncio.Event,
    ) -> dict[str, Any]:
        """Run ``tool.execute()`` with cancellation checking.

        The tool's ``execute()`` is awaited directly.  Cancellation is
        cooperative: the ``cancel_event`` is checked before and after
        execution.  For long-running tools, cancellation relies on
        ``asyncio.wait_for`` in the caller.
        """
        if cancel_event.is_set():
            raise asyncio.CancelledError("Execution cancelled before start")

        result = await tool.execute(params, context=context)

        if cancel_event.is_set():
            raise asyncio.CancelledError("Execution cancelled after completion")

        return result

    def _get_cancel_event(self, tool_id: str) -> asyncio.Event:
        """Get or create a cancel event for a tool execution."""
        if tool_id not in self._cancel_events:
            self._cancel_events[tool_id] = asyncio.Event()
        return self._cancel_events[tool_id]

    def _cleanup_cancel_event(self, tool_id: str) -> None:
        """Remove the cancel event for a finished execution."""
        self._cancel_events.pop(tool_id, None)


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "ToolExecutor",
]
