"""Tool Execution Pipeline — main orchestrator.

Sprint 4.3 — Tool Execution Pipeline.

Provides :class:`ToolExecutionPipeline`, the top-level orchestrator
for executing a tool that was previously selected by the
:class:`~src.selection.ToolSelectionEngine`.

The pipeline flow::

    1. receive  ToolSelectionResult
    2. validate (tool availability, permissions, arguments, security)
    3. prepare  (create ExecutionSession, merge context)
    4. execute  (via ToolExecutor with timeout)
    5. capture  (result from tool)
    6. validate output
    7. record  metrics
    8. return  ExecutionResult

The pipeline is **async-first**.  A synchronous convenience wrapper
:meth:`execute` is provided for non-async callers.

Configuration is via :class:`PipelineConfig`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.base import BaseTool, PermissionLevel
from src.exceptions import (
    ExecutionCancelledError,
    ExecutionTimeoutError,
    ToolExecutionError,
    ToolSelectionError,
)
from src.executor import DEFAULT_TIMEOUT_MS, ToolExecutor
from src.lifecycle import ExecutionLifecycleManager
from src.results import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSession,
    ExecutionStatus,
    ToolArguments,
    ToolSelectionResult,
)
from src.validation import (
    SecurityRestrictions,
    ToolExecutionValidator,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Configuration
# ======================================================================


@dataclass
class PipelineConfig:
    """Configuration for :class:`ToolExecutionPipeline`.

    Attributes:
        default_timeout_ms: Default execution timeout in milliseconds.
        max_permission_level: Maximum allowed permission level.
        security: Security restrictions applied during validation.
        sanitize_logs: Whether to mask sensitive arguments in logs.
    """

    default_timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_permission_level: PermissionLevel = PermissionLevel.HIGH
    security: SecurityRestrictions = field(
        default_factory=SecurityRestrictions,
    )
    sanitize_logs: bool = True


# ======================================================================
# Pipeline
# ======================================================================


class ToolExecutionPipeline:
    """Full execution pipeline: validate → prepare → execute → result.

    This is the main entry point for running a tool after it has been
    selected by the :class:`~src.selection.ToolSelectionEngine`.

    Args:
        config: Pipeline configuration.
        validator: Pre-configured validator. If ``None``, one is
            created from *config*.
        executor: Pre-configured executor. If ``None``, one is
            created from *config*.
        lifecycle: Pre-configured lifecycle manager. If ``None``, a
            new one is created.

    Usage::

        pipeline = ToolExecutionPipeline()
        result = await pipeline.execute_async(
            selection=ToolSelectionResult(tool=my_tool),
            arguments=ToolArguments(args={"path": "/tmp"}),
            exec_ctx=ExecutionContext(session_id="s1"),
        )
    """

    def __init__(
        self,
        *,
        config: PipelineConfig | None = None,
        validator: ToolExecutionValidator | None = None,
        executor: ToolExecutor | None = None,
        lifecycle: ExecutionLifecycleManager | None = None,
    ) -> None:
        self._config = config or PipelineConfig()
        self._validator = validator or ToolExecutionValidator(
            max_permission_level=self._config.max_permission_level,
            security=self._config.security,
        )
        self._executor = executor or ToolExecutor(
            default_timeout_ms=self._config.default_timeout_ms,
        )
        self._lifecycle = lifecycle or ExecutionLifecycleManager()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> PipelineConfig:
        return self._config

    @property
    def validator(self) -> ToolExecutionValidator:
        return self._validator

    @property
    def executor(self) -> ToolExecutor:
        return self._executor

    @property
    def lifecycle(self) -> ExecutionLifecycleManager:
        return self._lifecycle

    # ------------------------------------------------------------------
    # Async execution
    # ------------------------------------------------------------------

    async def execute_async(
        self,
        selection: ToolSelectionResult,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
        *,
        timeout_ms: int | None = None,
    ) -> ExecutionResult:
        """Execute a tool through the full pipeline (async).

        Args:
            selection: The tool selection result from the selection
                engine.
            arguments: Validated arguments for the tool.
            exec_ctx: Runtime execution context.
            timeout_ms: Override timeout for this execution.

        Returns:
            An :class:`ExecutionResult` with the outcome.

        Raises:
            ToolExecutionError: On any pipeline error.
        """
        tool = selection.tool
        t0 = time.monotonic()

        logger.info(
            "Pipeline: starting execution of tool '%s' " "(task='%s', score=%.4f)",
            tool.name,
            selection.task_name,
            selection.score,
        )

        # Step 1: Validate.
        session = self.validate_execution(
            tool,
            arguments,
            exec_ctx,
            selection,
        )

        try:
            # Step 2: Prepare.
            self.prepare_context(session, tool, arguments, exec_ctx)

            # Step 3: Execute.
            raw_result = await self._run_tool(
                tool,
                arguments,
                exec_ctx,
                session,
                timeout_ms,
            )

            # Step 4: Handle result.
            return self.handle_result(
                session,
                tool,
                raw_result,
                t0,
            )

        except asyncio.TimeoutError as exc:
            self._lifecycle.set_error(session, str(exc))
            self._lifecycle.transition(session, ExecutionStatus.TIMEOUT)
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "Pipeline: tool '%s' timed out after %dms",
                tool.name,
                elapsed,
            )
            raise ExecutionTimeoutError(
                f"Tool '{tool.name}' timed out",
                tool_name=tool.name,
                timeout_ms=timeout_ms or self.executor.timeout(tool),
            ) from exc

        except asyncio.CancelledError as exc:
            self._lifecycle.set_error(session, "Execution cancelled")
            self._lifecycle.transition(session, ExecutionStatus.CANCELLED)
            logger.info("Pipeline: tool '%s' cancelled", tool.name)
            raise ExecutionCancelledError(
                f"Tool '{tool.name}' was cancelled",
                tool_name=tool.name,
                reason="Cancelled by user or system",
            ) from exc

        except ToolExecutionError:
            raise

        except ToolSelectionError:
            raise

        except Exception as exc:
            self._lifecycle.set_error(session, str(exc))
            self._lifecycle.transition(session, ExecutionStatus.FAILED)
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.exception(
                "Pipeline: tool '%s' failed after %dms: %s",
                tool.name,
                elapsed,
                exc,
            )
            raise ToolExecutionError(
                f"Tool '{tool.name}' failed: {exc}",
                details={"tool_name": tool.name},
            ) from exc

    # ------------------------------------------------------------------
    # Sync execution
    # ------------------------------------------------------------------

    def execute(
        self,
        selection: ToolSelectionResult,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
        *,
        timeout_ms: int | None = None,
    ) -> ExecutionResult:
        """Execute a tool through the full pipeline (sync).

        Wraps :meth:`execute_async` for non-async callers.

        Args:
            selection: The tool selection result.
            arguments: Validated arguments.
            exec_ctx: Runtime context.
            timeout_ms: Override timeout.

        Returns:
            An :class:`ExecutionResult`.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=1,
            ) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.execute_async(
                        selection,
                        arguments,
                        exec_ctx,
                        timeout_ms=timeout_ms,
                    ),
                )
                return future.result()

        return asyncio.run(
            self.execute_async(
                selection,
                arguments,
                exec_ctx,
                timeout_ms=timeout_ms,
            ),
        )

    # ------------------------------------------------------------------
    # Pipeline steps (also usable individually)
    # ------------------------------------------------------------------

    def validate_execution(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
        selection: ToolSelectionResult | None = None,
    ) -> ExecutionSession:
        """Run validation and create a session.

        This is step 1 of the pipeline.  It validates the tool's
        availability, permissions, arguments, and security constraints.
        On success it creates an :class:`ExecutionSession` in
        ``VALIDATING`` status.

        Args:
            tool: The tool to validate.
            arguments: The arguments that will be passed.
            exec_ctx: The execution context.
            selection: Optional selection result for context.

        Returns:
            A new :class:`ExecutionSession`.

        Raises:
            ToolExecutionError: If validation fails.
        """
        logger.debug(
            "Pipeline: validating tool '%s'",
            tool.name,
        )

        session = self._lifecycle.create_session(tool, exec_ctx)
        self._lifecycle.transition(session, ExecutionStatus.VALIDATING)

        try:
            self._validator.validate(tool, arguments, exec_ctx)
        except ToolExecutionError:
            self._lifecycle.set_error(session, "Validation failed")
            self._lifecycle.transition(session, ExecutionStatus.FAILED)
            raise

        return session

    def prepare_context(
        self,
        session: ExecutionSession,
        tool: BaseTool,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Prepare the execution context dict for the tool.

        Merges the execution context with pipeline-specific metadata
        (session_id, selection info).  Transitions the session to
        ``PREPARING`` then ``RUNNING``.

        Args:
            session: The active execution session.
            tool: The tool about to be executed.
            arguments: The validated arguments.
            exec_ctx: The original execution context.

        Returns:
            A context dict suitable for ``BaseTool.execute()``.
        """
        self._lifecycle.transition(session, ExecutionStatus.PREPARING)

        context = exec_ctx.to_dict()
        context["pipeline_session_id"] = session.id

        # Log arguments (sanitised if configured).
        if self._config.sanitize_logs:
            log_args = ToolExecutionValidator.sanitize_args_for_logging(
                arguments,
            )
        else:
            log_args = arguments.args
        logger.info(
            "Pipeline: executing tool '%s' with args=%s " "(session=%s)",
            tool.name,
            log_args,
            session.id,
        )

        self._lifecycle.transition(session, ExecutionStatus.RUNNING)
        return context

    def handle_result(
        self,
        session: ExecutionSession,
        tool: BaseTool,
        raw_result: dict[str, Any],
        start_time: float,
    ) -> ExecutionResult:
        """Process the raw result from a tool execution.

        Validates output, transitions the session to ``COMPLETED`` or
        ``FAILED``, and constructs an :class:`ExecutionResult`.

        Args:
            session: The active execution session.
            tool: The tool that was executed.
            raw_result: The dict returned by ``tool.execute()``.
            start_time: The ``time.monotonic()`` when the pipeline
                started.

        Returns:
            A structured :class:`ExecutionResult`.
        """
        elapsed = int((time.monotonic() - start_time) * 1000)

        success = raw_result.get("success", False)

        if success:
            self._lifecycle.transition(session, ExecutionStatus.COMPLETED)
            logger.info(
                "Pipeline: tool '%s' completed in %dms " "(session=%s)",
                tool.name,
                elapsed,
                session.id,
            )
        else:
            error_msg = raw_result.get("error", "Tool returned success=False")
            self._lifecycle.set_error(session, error_msg)
            self._lifecycle.transition(session, ExecutionStatus.FAILED)
            logger.warning(
                "Pipeline: tool '%s' returned failure in %dms: %s " "(session=%s)",
                tool.name,
                elapsed,
                error_msg,
                session.id,
            )

        result = ExecutionResult(
            success=success,
            tool_name=tool.name,
            output=raw_result.get("output"),
            error=raw_result.get("error"),
            execution_time_ms=elapsed,
            metadata={
                "_tool": tool,
                "session_id": session.id,
                "tool_id": tool.tool_id,
                "duration_ms": raw_result.get("duration_ms"),
            },
        )
        return result

    def handle_error(
        self,
        session: ExecutionSession,
        tool: BaseTool,
        error: Exception,
        start_time: float,
    ) -> ExecutionResult:
        """Create an :class:`ExecutionResult` from an exception.

        Transitions the session to ``FAILED``, records the error, and
        returns a failure result.

        Args:
            session: The active execution session.
            tool: The tool that was being executed.
            error: The exception that was raised.
            start_time: The ``time.monotonic()`` when the pipeline
                started.

        Returns:
            A failure :class:`ExecutionResult`.
        """
        elapsed = int((time.monotonic() - start_time) * 1000)
        error_msg = str(error)

        self._lifecycle.set_error(session, error_msg)
        try:
            self._lifecycle.transition(session, ExecutionStatus.FAILED)
        except ValueError:
            # Session may already be in a terminal state (e.g. TIMEOUT).
            pass

        logger.error(
            "Pipeline: tool '%s' failed after %dms: %s (session=%s)",
            tool.name,
            elapsed,
            error_msg,
            session.id,
        )

        return ExecutionResult(
            success=False,
            tool_name=tool.name,
            error=error_msg,
            execution_time_ms=elapsed,
            metadata={
                "_tool": tool,
                "session_id": session.id,
                "tool_id": tool.tool_id,
                "error_type": type(error).__name__,
            },
        )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, tool_id: str) -> bool:
        """Cancel a running tool execution by its tool_id.

        Args:
            tool_id: The ``tool_id`` of the tool to cancel.

        Returns:
            ``True`` if a cancellation signal was sent.
        """
        return self._executor.cancel(tool_id)

    def cancel_all(self) -> int:
        """Cancel all running executions.

        Returns:
            The number of executions signalled for cancellation.
        """
        return self._executor.cancel_all()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_tool(
        self,
        tool: BaseTool,
        arguments: ToolArguments,
        exec_ctx: ExecutionContext,
        session: ExecutionSession,
        timeout_ms: int | None,
    ) -> dict[str, Any]:
        """Execute the tool via the executor."""
        context = exec_ctx.to_dict()
        context["pipeline_session_id"] = session.id

        return await self._executor.run_async(
            tool,
            arguments,
            context,
            timeout_ms=timeout_ms,
        )


__all__ = [
    "PipelineConfig",
    "ToolExecutionPipeline",
]
