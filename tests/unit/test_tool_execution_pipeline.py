"""Tests for the Tool Execution Pipeline — Sprint 4.3.

Covers:
  - results.py     (ExecutionSession, ExecutionResult, ExecutionStatus,
                    ToolArguments, ExecutionContext, ToolSelectionResult)
  - exceptions.py  (execution-specific: ToolExecutionError and subclasses)
  - lifecycle.py   (ExecutionLifecycleManager)
  - validation.py  (ToolExecutionValidator, SecurityRestrictions)
  - executor.py    (ToolExecutor)
  - execution.py   (ToolExecutionPipeline)
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

sys.path.insert(0, "packages/tools")
from tests.conftest import clear_src_cache  # noqa: E402

clear_src_cache()

import pytest  # noqa: E402

from src.base import (  # noqa: E402
    BaseTool,
    PermissionLevel,
    ToolCategory,
    ToolState,
)
from src.exceptions import (  # noqa: E402
    ExecutionCancelledError,
    ExecutionTimeoutError,
    ExecutionValidationError,
    InvalidArgumentsError,
    PermissionDeniedError,
    ToolExecutionError,
)
from src.execution import PipelineConfig, ToolExecutionPipeline  # noqa: E402
from src.executor import DEFAULT_TIMEOUT_MS, ToolExecutor  # noqa: E402
from src.lifecycle import ExecutionLifecycleManager  # noqa: E402
from src.results import (  # noqa: E402
    ExecutionContext,
    ExecutionResult,
    ExecutionSession,
    ExecutionStatus,
    ToolArguments,
    ToolSelectionResult,
)
from src.validation import (  # noqa: E402
    SecurityRestrictions,
    ToolExecutionValidator,
)

# ======================================================================
# Helpers
# ======================================================================


class _FakeTool(BaseTool):
    """A simple test tool that returns a configurable result."""

    def __init__(
        self,
        name: str = "fake",
        *,
        category: ToolCategory = ToolCategory.CUSTOM,
        permission_level: PermissionLevel = PermissionLevel.LOW,
        state: ToolState = ToolState.AVAILABLE,
        parameters_schema: dict[str, Any] | None = None,
        return_success: bool = True,
        return_output: Any = None,
        return_error: str | None = None,
        sleep_seconds: float = 0.0,
        raise_on_execute: Exception | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._return_success = return_success
        self._return_output = return_output
        self._return_error = return_error
        self._sleep_seconds = sleep_seconds
        self._raise_on_execute = raise_on_execute
        self._override_timeout = timeout_ms
        # Set parameters_schema before __init__ since BaseTool reads it.
        if parameters_schema is not None:
            self.parameters_schema = parameters_schema
        super().__init__(name=name)
        self.category = category
        self.permission_level = permission_level
        if state != ToolState.AVAILABLE:
            self._state = state

    @property
    def metadata(self) -> dict[str, Any]:
        base = super().metadata
        if self._override_timeout is not None:
            base["timeout_ms"] = self._override_timeout
        return dict(base)

    async def _execute(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        if self._sleep_seconds > 0:
            await asyncio.sleep(self._sleep_seconds)
        result: dict[str, Any] = {
            "success": self._return_success,
            "output": self._return_output,
        }
        if self._return_error is not None:
            result["error"] = self._return_error
        return result


def _make_tool(**kwargs: Any) -> _FakeTool:
    return _FakeTool(**kwargs)


def _make_selection(tool: BaseTool, **kwargs: Any) -> ToolSelectionResult:
    defaults = {"score": 0.95, "reason": "test", "task_name": "test_task"}
    defaults.update(kwargs)
    return ToolSelectionResult(tool=tool, **defaults)


def _make_args(**kwargs: Any) -> ToolArguments:
    return ToolArguments(args=kwargs)


def _make_ctx(**kwargs: Any) -> ExecutionContext:
    defaults = {
        "session_id": "sess-1",
        "agent_name": "test-agent",
        "user_id": "user-1",
    }
    defaults.update(kwargs)
    return ExecutionContext(**defaults)


# ======================================================================
# ExecutionStatus
# ======================================================================


class TestExecutionStatus:
    def test_values(self) -> None:
        assert ExecutionStatus.CREATED.value == "created"
        assert ExecutionStatus.COMPLETED.value == "completed"
        assert ExecutionStatus.FAILED.value == "failed"
        assert ExecutionStatus.TIMEOUT.value == "timeout"
        assert ExecutionStatus.CANCELLED.value == "cancelled"

    def test_terminal_states(self) -> None:
        assert ExecutionStatus.COMPLETED.is_terminal is True
        assert ExecutionStatus.FAILED.is_terminal is True
        assert ExecutionStatus.TIMEOUT.is_terminal is True
        assert ExecutionStatus.CANCELLED.is_terminal is True
        assert ExecutionStatus.CREATED.is_terminal is False
        assert ExecutionStatus.VALIDATING.is_terminal is False
        assert ExecutionStatus.PREPARING.is_terminal is False
        assert ExecutionStatus.RUNNING.is_terminal is False


# ======================================================================
# ExecutionSession
# ======================================================================


class TestExecutionSession:
    def test_default_state(self) -> None:
        session = ExecutionSession()
        assert session.status == ExecutionStatus.CREATED
        assert session.tool_id == ""
        assert session.duration_ms is None
        assert session.finished_at is None
        assert session.error is None
        assert session.events == []

    def test_transition_records_event(self) -> None:
        session = ExecutionSession()
        session.transition(ExecutionStatus.VALIDATING)
        assert session.status == ExecutionStatus.VALIDATING
        assert len(session.events) == 1
        assert session.events[0]["from"] == "created"
        assert session.events[0]["to"] == "validating"

    def test_full_pipeline(self) -> None:
        session = ExecutionSession(tool_name="test_tool")
        for new_status in [
            ExecutionStatus.VALIDATING,
            ExecutionStatus.PREPARING,
            ExecutionStatus.RUNNING,
            ExecutionStatus.COMPLETED,
        ]:
            session.transition(new_status)
        assert session.status == ExecutionStatus.COMPLETED
        assert session.finished_at is not None
        assert session.duration_ms is not None
        assert len(session.events) == 4

    def test_same_status_raises(self) -> None:
        session = ExecutionSession()
        with pytest.raises(ValueError, match="same status"):
            session.transition(ExecutionStatus.CREATED)

    def test_terminal_transition_raises(self) -> None:
        session = ExecutionSession()
        session.transition(ExecutionStatus.VALIDATING)
        session.transition(ExecutionStatus.COMPLETED)
        with pytest.raises(ValueError, match="terminal state"):
            session.transition(ExecutionStatus.RUNNING)

    def test_to_dict(self) -> None:
        session = ExecutionSession(
            tool_id="abc123",
            tool_name="my_tool",
        )
        d = session.to_dict()
        assert d["tool_id"] == "abc123"
        assert d["tool_name"] == "my_tool"
        assert d["status"] == "created"
        assert "started_at" in d
        assert d["finished_at"] is None

    def test_failed_terminal_sets_finished_at(self) -> None:
        session = ExecutionSession()
        session.transition(ExecutionStatus.RUNNING)
        session.transition(ExecutionStatus.FAILED)
        assert session.finished_at is not None
        assert session.duration_ms is not None


# ======================================================================
# ExecutionResult
# ======================================================================


class TestExecutionResult:
    def test_success_result(self) -> None:
        result = ExecutionResult(
            success=True,
            tool_name="tool_a",
            output={"data": 42},
            execution_time_ms=100,
        )
        assert result.success is True
        assert result.tool_name == "tool_a"
        assert result.output == {"data": 42}
        assert result.error is None

    def test_failure_result(self) -> None:
        result = ExecutionResult(
            success=False,
            tool_name="tool_b",
            error="Something went wrong",
            execution_time_ms=50,
        )
        assert result.success is False
        assert result.error == "Something went wrong"

    def test_tool_property(self) -> None:
        tool = _make_tool(name="ref_tool")
        result = ExecutionResult(
            success=True,
            tool_name="ref_tool",
            metadata={"_tool": tool},
        )
        assert result.tool is tool

    def test_tool_property_none(self) -> None:
        result = ExecutionResult(success=True, tool_name="x")
        assert result.tool is None

    def test_to_dict_excludes_tool(self) -> None:
        tool = _make_tool()
        result = ExecutionResult(
            success=True,
            tool_name="t",
            metadata={"_tool": tool, "session_id": "s1"},
        )
        d = result.to_dict()
        assert "_tool" not in d["metadata"]
        assert d["metadata"]["session_id"] == "s1"


# ======================================================================
# ToolArguments
# ======================================================================


class TestToolArguments:
    def test_empty(self) -> None:
        args = ToolArguments()
        assert len(args) == 0
        assert "key" not in args
        assert args.get("key") is None

    def test_with_args(self) -> None:
        args = ToolArguments(args={"a": 1, "b": "hello"})
        assert len(args) == 2
        assert "a" in args
        assert args.get("a") == 1
        assert args.get("b") == "hello"
        assert args.get("missing", "default") == "default"

    def test_frozen_prevents_reassignment(self) -> None:
        args = ToolArguments(args={"x": 1})
        with pytest.raises(AttributeError):
            args.args = {"y": 2}


# ======================================================================
# ExecutionContext
# ======================================================================


class TestExecutionContext:
    def test_defaults(self) -> None:
        ctx = ExecutionContext()
        assert ctx.session_id == ""
        assert ctx.agent_name == ""

    def test_with_values(self) -> None:
        ctx = ExecutionContext(
            session_id="s1",
            agent_name="agent-1",
            user_id="u1",
        )
        assert ctx.session_id == "s1"
        assert ctx.agent_name == "agent-1"
        assert ctx.user_id == "u1"

    def test_to_dict(self) -> None:
        ctx = ExecutionContext(
            session_id="s1",
            agent_name="agent-1",
            extra={"custom_key": "custom_val"},
        )
        d = ctx.to_dict()
        assert d["session_id"] == "s1"
        assert d["agent_name"] == "agent-1"
        assert d["custom_key"] == "custom_val"

    def test_frozen(self) -> None:
        ctx = ExecutionContext()
        with pytest.raises(AttributeError):
            ctx.session_id = "new"


# ======================================================================
# ToolSelectionResult
# ======================================================================


class TestToolSelectionResult:
    def test_basic(self) -> None:
        tool = _make_tool(name="test")
        sel = ToolSelectionResult(
            tool=tool,
            score=0.9,
            reason="Best match",
            task_name="task1",
        )
        assert sel.tool is tool
        assert sel.score == 0.9
        assert sel.reason == "Best match"
        assert sel.task_name == "task1"

    def test_frozen(self) -> None:
        sel = ToolSelectionResult(tool=_make_tool())
        with pytest.raises(AttributeError):
            sel.score = 0.5


# ======================================================================
# Execution exceptions
# ======================================================================


class TestExecutionExceptions:
    def test_tool_execution_error_base(self) -> None:
        err = ToolExecutionError("base error", details={"key": "val"})
        assert str(err) == "base error"
        assert err.details == {"key": "val"}
        assert isinstance(err, Exception)

    def test_invalid_arguments_error(self) -> None:
        err = InvalidArgumentsError(
            "bad arg",
            field="path",
            expected="str",
            actual="int",
        )
        assert err.field == "path"
        assert err.expected == "str"
        assert err.actual == "int"
        assert err.details["field"] == "path"
        assert isinstance(err, ToolExecutionError)

    def test_permission_denied_error(self) -> None:
        err = PermissionDeniedError(
            "denied",
            tool_name="terminal",
            required_level="high",
            max_level="medium",
        )
        assert err.tool_name == "terminal"
        assert err.required_level == "high"
        assert err.max_level == "medium"
        assert isinstance(err, ToolExecutionError)

    def test_execution_validation_error(self) -> None:
        err = ExecutionValidationError(
            "validation fail",
            validation_type="availability",
        )
        assert err.validation_type == "availability"
        assert err.details["validation_type"] == "availability"
        assert isinstance(err, ToolExecutionError)

    def test_execution_timeout_error(self) -> None:
        err = ExecutionTimeoutError(
            "timed out",
            tool_name="slow_tool",
            timeout_ms=5000,
        )
        assert err.tool_name == "slow_tool"
        assert err.timeout_ms == 5000
        assert err.details["timeout_ms"] == "5000"
        assert isinstance(err, ToolExecutionError)

    def test_execution_cancelled_error(self) -> None:
        err = ExecutionCancelledError(
            "cancelled",
            tool_name="tool_x",
            reason="user request",
        )
        assert err.tool_name == "tool_x"
        assert err.reason == "user request"
        assert err.details["reason"] == "user request"
        assert isinstance(err, ToolExecutionError)

    def test_hierarchy_catch_all(self) -> None:
        for cls in [
            InvalidArgumentsError,
            PermissionDeniedError,
            ExecutionValidationError,
            ExecutionTimeoutError,
            ExecutionCancelledError,
        ]:
            err = cls("test")
            assert isinstance(err, ToolExecutionError)


# ======================================================================
# ExecutionLifecycleManager
# ======================================================================


class TestExecutionLifecycleManager:
    def test_create_session(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool(name="t1")
        ctx = _make_ctx()
        session = mgr.create_session(tool, ctx)
        assert session.tool_id == tool.tool_id
        assert session.tool_name == "t1"
        assert session.status == ExecutionStatus.CREATED
        assert session.id in [s.id for s in mgr.all_sessions()]

    def test_transition(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool()
        ctx = _make_ctx()
        session = mgr.create_session(tool, ctx)
        mgr.transition(session, ExecutionStatus.VALIDATING)
        assert session.status == ExecutionStatus.VALIDATING

    def test_transition_untracked_raises(self) -> None:
        mgr = ExecutionLifecycleManager()
        session = ExecutionSession()
        with pytest.raises(KeyError, match="not managed"):
            mgr.transition(session, ExecutionStatus.VALIDATING)

    def test_set_error(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool()
        session = mgr.create_session(tool, _make_ctx())
        mgr.set_error(session, "Boom!")
        assert session.error == "Boom!"

    def test_get_session(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool()
        session = mgr.create_session(tool, _make_ctx())
        found = mgr.get_session(session.id)
        assert found is session
        assert mgr.get_session("nonexistent") is None

    def test_active_sessions(self) -> None:
        mgr = ExecutionLifecycleManager()
        s1 = mgr.create_session(_make_tool(name="a"), _make_ctx())
        s2 = mgr.create_session(_make_tool(name="b"), _make_ctx())
        mgr.transition(s1, ExecutionStatus.COMPLETED)
        active = mgr.active_sessions()
        assert s1 not in active
        assert s2 in active

    def test_sessions_for_tool(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool(name="same_tool")
        s1 = mgr.create_session(tool, _make_ctx())
        mgr.transition(s1, ExecutionStatus.COMPLETED)
        s2 = mgr.create_session(tool, _make_ctx())
        sessions = mgr.sessions_for_tool(tool.tool_id)
        assert len(sessions) == 2
        # Newest first.
        assert sessions[0].id == s2.id
        assert sessions[1].id == s1.id

    def test_overlapping_session_raises(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool(name="overlap")
        mgr.create_session(tool, _make_ctx())
        # Same tool, non-terminal session exists.
        with pytest.raises(RuntimeError, match="already has an active session"):
            mgr.create_session(tool, _make_ctx())

    def test_summary(self) -> None:
        mgr = ExecutionLifecycleManager()
        s1 = mgr.create_session(_make_tool(name="a"), _make_ctx())
        mgr.create_session(_make_tool(name="b"), _make_ctx())
        mgr.transition(s1, ExecutionStatus.COMPLETED)
        summary = mgr.summary()
        assert summary["total"] == 2
        assert summary["active"] == 1
        assert summary["by_status"]["completed"] == 1
        assert summary["by_status"]["created"] == 1

    def test_extra_context(self) -> None:
        mgr = ExecutionLifecycleManager()
        tool = _make_tool()
        ctx = _make_ctx()
        session = mgr.create_session(
            tool,
            ctx,
            extra_context={"custom": "data"},
        )
        assert session.context["custom"] == "data"
        assert session.context["session_id"] == "sess-1"


# ======================================================================
# ToolExecutionValidator
# ======================================================================


class TestToolExecutionValidator:
    def test_validate_available_tool(self) -> None:
        validator = ToolExecutionValidator()
        tool = _make_tool(name="ok")
        args = _make_args(x=1)
        ctx = _make_ctx()
        # Should not raise.
        validator.validate(tool, args, ctx)

    def test_validate_disabled_tool_raises(self) -> None:
        validator = ToolExecutionValidator()
        tool = _make_tool(name="disabled", state=ToolState.DISABLED)
        args = _make_args()
        ctx = _make_ctx()
        with pytest.raises(ExecutionValidationError, match="not available"):
            validator.validate(tool, args, ctx)

    def test_validate_permission_denied(self) -> None:
        validator = ToolExecutionValidator(
            max_permission_level=PermissionLevel.LOW,
        )
        tool = _make_tool(
            name="high_tool",
            permission_level=PermissionLevel.HIGH,
        )
        args = _make_args()
        ctx = _make_ctx()
        with pytest.raises(PermissionDeniedError, match="high"):
            validator.validate(tool, args, ctx)

    def test_validate_permission_allowed(self) -> None:
        validator = ToolExecutionValidator(
            max_permission_level=PermissionLevel.HIGH,
        )
        tool = _make_tool(
            name="medium_tool",
            permission_level=PermissionLevel.MEDIUM,
        )
        args = _make_args()
        ctx = _make_ctx()
        validator.validate(tool, args, ctx)

    def test_missing_required_field(self) -> None:
        validator = ToolExecutionValidator()
        tool = _make_tool(
            name="schema_tool",
            parameters_schema={
                "required": ["path"],
                "properties": {
                    "path": {"type": "str", "description": "File path"},
                },
            },
        )
        args = _make_args()  # missing "path"
        ctx = _make_ctx()
        with pytest.raises(InvalidArgumentsError, match="Missing required"):
            validator.validate(tool, args, ctx)

    def test_wrong_type(self) -> None:
        validator = ToolExecutionValidator()
        tool = _make_tool(
            name="typed_tool",
            parameters_schema={
                "required": ["count"],
                "properties": {
                    "count": {"type": "int", "description": "Count"},
                },
            },
        )
        args = _make_args(count="not_an_int")
        ctx = _make_ctx()
        with pytest.raises(InvalidArgumentsError, match="must be of type"):
            validator.validate(tool, args, ctx)

    def test_valid_schema_args(self) -> None:
        validator = ToolExecutionValidator()
        tool = _make_tool(
            name="valid_tool",
            parameters_schema={
                "required": ["name", "count"],
                "properties": {
                    "name": {"type": "str"},
                    "count": {"type": "int"},
                },
            },
        )
        args = _make_args(name="test", count=5)
        ctx = _make_ctx()
        validator.validate(tool, args, ctx)

    def test_security_blocked_value(self) -> None:
        validator = ToolExecutionValidator(
            security=SecurityRestrictions(blocked_values=["rm -rf"]),
        )
        tool = _make_tool()
        args = _make_args(cmd="rm -rf /")
        ctx = _make_ctx()
        with pytest.raises(ToolExecutionError, match="blocked value"):
            validator.validate(tool, args, ctx)

    def test_security_blocked_pattern(self) -> None:
        validator = ToolExecutionValidator(
            security=SecurityRestrictions(blocked_patterns=[r"DROP\s+TABLE"]),
        )
        tool = _make_tool()
        args = _make_args(query="DROP TABLE users")
        ctx = _make_ctx()
        with pytest.raises(ToolExecutionError, match="blocked pattern"):
            validator.validate(tool, args, ctx)

    def test_security_max_argument_count(self) -> None:
        validator = ToolExecutionValidator(
            security=SecurityRestrictions(max_argument_count=2),
        )
        tool = _make_tool()
        args = _make_args(a=1, b=2, c=3)
        ctx = _make_ctx()
        with pytest.raises(ToolExecutionError, match="Too many arguments"):
            validator.validate(tool, args, ctx)

    def test_sanitize_args_for_logging(self) -> None:
        args = ToolArguments(
            args={"name": "test", "password": "secret123", "token": "abc"},
        )
        sanitized = ToolExecutionValidator.sanitize_args_for_logging(args)
        assert sanitized["name"] == "test"
        assert sanitized["password"] == "***"
        assert sanitized["token"] == "***"

    def test_sanitize_no_sensitive_keys(self) -> None:
        args = ToolArguments(args={"foo": "bar", "baz": 42})
        sanitized = ToolExecutionValidator.sanitize_args_for_logging(args)
        assert sanitized == {"foo": "bar", "baz": 42}


# ======================================================================
# ToolExecutor
# ======================================================================


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_run_async_success(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(name="fast", return_output={"result": "ok"})
        args = _make_args()
        ctx = _make_ctx().to_dict()
        result = await executor.run_async(tool, args, ctx)
        assert result["success"] is True
        assert result["output"] == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_run_async_failure(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(
            name="failing",
            return_success=False,
            return_error="Intentional failure",
        )
        args = _make_args()
        ctx = _make_ctx().to_dict()
        result = await executor.run_async(tool, args, ctx)
        assert result["success"] is False
        assert result["error"] == "Intentional failure"

    @pytest.mark.asyncio
    async def test_run_async_tool_raises(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(
            name="error_tool",
            raise_on_execute=RuntimeError("Boom"),
        )
        args = _make_args()
        ctx = _make_ctx().to_dict()
        with pytest.raises(RuntimeError, match="Boom"):
            await executor.run_async(tool, args, ctx)

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        executor = ToolExecutor(default_timeout_ms=100)
        tool = _make_tool(name="slow", sleep_seconds=10.0)
        args = _make_args()
        ctx = _make_ctx().to_dict()
        with pytest.raises(asyncio.TimeoutError):
            await executor.run_async(tool, args, ctx)

    @pytest.mark.asyncio
    async def test_per_tool_timeout(self) -> None:
        executor = ToolExecutor(default_timeout_ms=30_000)
        tool = _make_tool(
            name="slow_tool",
            sleep_seconds=10.0,
            timeout_ms=50,
        )
        args = _make_args()
        ctx = _make_ctx().to_dict()
        with pytest.raises(asyncio.TimeoutError):
            await executor.run_async(tool, args, ctx)

    @pytest.mark.asyncio
    async def test_explicit_timeout_override(self) -> None:
        executor = ToolExecutor(default_timeout_ms=30_000)
        tool = _make_tool(name="slow", sleep_seconds=10.0)
        args = _make_args()
        ctx = _make_ctx().to_dict()
        with pytest.raises(asyncio.TimeoutError):
            await executor.run_async(
                tool,
                args,
                ctx,
                timeout_ms=100,
            )

    @pytest.mark.asyncio
    async def test_cancel(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(name="cancelable", sleep_seconds=10.0)
        args = _make_args()

        async def _run_and_cancel() -> None:
            task = asyncio.create_task(
                executor.run_async(tool, args, {}),
            )
            # Give the task a moment to start.
            await asyncio.sleep(0.05)
            cancelled = executor.cancel(tool.tool_id)
            assert cancelled is True
            with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
                await task

        await _run_and_cancel()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self) -> None:
        executor = ToolExecutor()
        assert executor.cancel("nonexistent") is False

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(name="t", sleep_seconds=10.0)
        args = _make_args()
        task = asyncio.create_task(
            executor.run_async(tool, args, {}),
        )
        await asyncio.sleep(0.05)
        count = executor.cancel_all()
        assert count >= 1
        with pytest.raises((asyncio.CancelledError, asyncio.TimeoutError)):
            await task

    def test_timeout_property(self) -> None:
        executor = ToolExecutor(default_timeout_ms=10_000)
        tool = _make_tool()
        assert executor.timeout(tool) == 10_000

    def test_timeout_property_tool_metadata(self) -> None:
        executor = ToolExecutor(default_timeout_ms=10_000)
        tool = _make_tool(timeout_ms=5000)
        assert executor.timeout(tool) == 5000

    def test_run_sync(self) -> None:
        executor = ToolExecutor()
        tool = _make_tool(name="sync_tool", return_output="data")
        args = _make_args()
        ctx = _make_ctx().to_dict()
        result = executor.run(tool, args, ctx)
        assert result["success"] is True
        assert result["output"] == "data"


# ======================================================================
# ToolExecutionPipeline
# ======================================================================


class TestToolExecutionPipeline:
    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="ok", return_output={"key": "val"})
        selection = _make_selection(tool)
        args = _make_args(action="test")
        ctx = _make_ctx()

        result = await pipeline.execute_async(selection, args, ctx)

        assert result.success is True
        assert result.tool_name == "ok"
        assert result.output == {"key": "val"}
        assert result.error is None
        assert result.execution_time_ms >= 0
        assert result.metadata["session_id"]
        assert result.metadata["tool_id"] == tool.tool_id

    @pytest.mark.asyncio
    async def test_tool_returns_failure(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(
            name="fail_tool",
            return_success=False,
            return_error="Tool error",
        )
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        result = await pipeline.execute_async(selection, args, ctx)

        assert result.success is False
        assert result.error == "Tool error"
        assert result.tool_name == "fail_tool"

    @pytest.mark.asyncio
    async def test_tool_raises_exception(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(
            name="error_tool",
            raise_on_execute=RuntimeError("Internal error"),
        )
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        with pytest.raises(ToolExecutionError, match="Internal error"):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_permission_denied(self) -> None:
        pipeline = ToolExecutionPipeline(
            config=PipelineConfig(
                max_permission_level=PermissionLevel.LOW,
            ),
        )
        tool = _make_tool(
            name="high_tool",
            permission_level=PermissionLevel.HIGH,
        )
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        with pytest.raises(PermissionDeniedError):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_invalid_arguments(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(
            name="typed",
            parameters_schema={
                "required": ["path"],
                "properties": {"path": {"type": "str"}},
            },
        )
        selection = _make_selection(tool)
        args = _make_args()  # missing required "path"
        ctx = _make_ctx()

        with pytest.raises(InvalidArgumentsError, match="Missing required"):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        pipeline = ToolExecutionPipeline(
            config=PipelineConfig(default_timeout_ms=100),
        )
        tool = _make_tool(name="slow", sleep_seconds=10.0)
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        with pytest.raises(ExecutionTimeoutError, match="timed out"):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_pipeline_timeout_override(self) -> None:
        pipeline = ToolExecutionPipeline(
            config=PipelineConfig(default_timeout_ms=30_000),
        )
        tool = _make_tool(name="slow", sleep_seconds=10.0)
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        with pytest.raises(ExecutionTimeoutError):
            await pipeline.execute_async(
                selection,
                args,
                ctx,
                timeout_ms=100,
            )

    @pytest.mark.asyncio
    async def test_cancel_execution(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="cancel_me", sleep_seconds=10.0)
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        async def _run_and_cancel() -> None:
            task = asyncio.create_task(
                pipeline.execute_async(selection, args, ctx),
            )
            await asyncio.sleep(0.05)
            pipeline.cancel(tool.tool_id)
            with pytest.raises(
                (ExecutionCancelledError, asyncio.TimeoutError),
            ):
                await task

        await _run_and_cancel()

    @pytest.mark.asyncio
    async def test_validate_execution_step(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="v")
        args = _make_args()
        ctx = _make_ctx()
        selection = _make_selection(tool)

        session = pipeline.validate_execution(tool, args, ctx, selection)
        assert session.status == ExecutionStatus.VALIDATING
        assert session.tool_name == "v"

    @pytest.mark.asyncio
    async def test_validate_execution_failure(self) -> None:
        pipeline = ToolExecutionPipeline(
            config=PipelineConfig(
                max_permission_level=PermissionLevel.LOW,
            ),
        )
        tool = _make_tool(
            name="hi",
            permission_level=PermissionLevel.HIGH,
        )
        args = _make_args()
        ctx = _make_ctx()
        selection = _make_selection(tool)

        with pytest.raises(PermissionDeniedError):
            pipeline.validate_execution(tool, args, ctx, selection)

    @pytest.mark.asyncio
    async def test_prepare_context_step(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="p")
        args = _make_args(x=1)
        ctx = _make_ctx(session_id="s99")

        # Create session manually for the step test.
        session = pipeline.lifecycle.create_session(tool, ctx)
        pipeline.lifecycle.transition(session, ExecutionStatus.VALIDATING)

        context = pipeline.prepare_context(session, tool, args, ctx)
        assert context["session_id"] == "s99"
        assert context["pipeline_session_id"] == session.id
        assert session.status == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_handle_result_success(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="h")
        session = pipeline.lifecycle.create_session(tool, _make_ctx())
        pipeline.lifecycle.transition(session, ExecutionStatus.RUNNING)

        raw = {"success": True, "output": "data", "duration_ms": 42}
        start = time.monotonic()

        result = pipeline.handle_result(session, tool, raw, start)
        assert result.success is True
        assert result.output == "data"
        assert session.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_handle_result_failure(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="h2")
        session = pipeline.lifecycle.create_session(tool, _make_ctx())
        pipeline.lifecycle.transition(session, ExecutionStatus.RUNNING)

        raw = {"success": False, "error": "Oops"}
        start = time.monotonic()

        result = pipeline.handle_result(session, tool, raw, start)
        assert result.success is False
        assert result.error == "Oops"
        assert session.status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_handle_error(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="h3")
        session = pipeline.lifecycle.create_session(tool, _make_ctx())
        pipeline.lifecycle.transition(session, ExecutionStatus.RUNNING)

        error = RuntimeError("crash")
        start = time.monotonic()

        result = pipeline.handle_error(session, tool, error, start)
        assert result.success is False
        assert "crash" in result.error
        assert session.status == ExecutionStatus.FAILED
        assert session.error == "crash"

    @pytest.mark.asyncio
    async def test_session_lifecycle_tracked(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="tracked")
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        await pipeline.execute_async(selection, args, ctx)

        sessions = pipeline.lifecycle.all_sessions()
        assert len(sessions) == 1
        session = sessions[0]
        assert session.status == ExecutionStatus.COMPLETED
        assert session.tool_name == "tracked"
        assert session.duration_ms is not None

    @pytest.mark.asyncio
    async def test_sync_execute(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="sync", return_output="sync_data")
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        result = pipeline.execute(selection, args, ctx)
        assert result.success is True
        assert result.output == "sync_data"

    @pytest.mark.asyncio
    async def test_cancel_all(self) -> None:
        pipeline = ToolExecutionPipeline()
        assert pipeline.cancel_all() == 0

    @pytest.mark.asyncio
    async def test_custom_validator_and_executor(self) -> None:
        validator = ToolExecutionValidator(
            max_permission_level=PermissionLevel.HIGH,
        )
        executor = ToolExecutor(default_timeout_ms=60_000)
        lifecycle = ExecutionLifecycleManager()
        pipeline = ToolExecutionPipeline(
            validator=validator,
            executor=executor,
            lifecycle=lifecycle,
        )
        tool = _make_tool(name="custom", return_output="custom_result")
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        result = await pipeline.execute_async(selection, args, ctx)
        assert result.success is True
        assert result.output == "custom_result"
        # Verify the custom lifecycle tracked it.
        assert len(lifecycle.all_sessions()) == 1

    @pytest.mark.asyncio
    async def test_disabled_tool_rejected(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="disabled", state=ToolState.DISABLED)
        selection = _make_selection(tool)
        args = _make_args()
        ctx = _make_ctx()

        with pytest.raises(ExecutionValidationError, match="not available"):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_pipeline_config_defaults(self) -> None:
        config = PipelineConfig()
        assert config.default_timeout_ms == DEFAULT_TIMEOUT_MS
        assert config.max_permission_level == PermissionLevel.HIGH
        assert config.sanitize_logs is True

    @pytest.mark.asyncio
    async def test_security_restrictions_in_pipeline(self) -> None:
        pipeline = ToolExecutionPipeline(
            config=PipelineConfig(
                security=SecurityRestrictions(
                    blocked_values=["dangerous"],
                ),
            ),
        )
        tool = _make_tool()
        selection = _make_selection(tool)
        args = _make_args(cmd="dangerous command")
        ctx = _make_ctx()

        with pytest.raises(ToolExecutionError, match="blocked value"):
            await pipeline.execute_async(selection, args, ctx)

    @pytest.mark.asyncio
    async def test_metadata_captured(self) -> None:
        pipeline = ToolExecutionPipeline()
        tool = _make_tool(name="meta_tool", return_output={"x": 1})
        selection = _make_selection(tool, score=0.88, task_name="meta_task")
        args = _make_args()
        ctx = _make_ctx(session_id="s-meta")

        result = await pipeline.execute_async(selection, args, ctx)
        assert result.metadata["session_id"]
        assert result.metadata["tool_id"] == tool.tool_id
        assert result.tool is tool
