"""Custom exceptions for the Tool Intelligence system.

Sprint 4.2 — Selection, ranking, and filtering errors.
Sprint 4.3 — Execution pipeline errors.

Provides a structured exception hierarchy so callers can distinguish
between different failure modes programmatically.
"""

from __future__ import annotations


class ToolSelectionError(Exception):
    """Base exception for all tool-selection errors.

    Every exception raised by the selection engine inherits from this
    class, allowing callers to catch any selection-related failure with
    a single ``except ToolSelectionError`` handler.
    """

    def __init__(self, message: str, *, details: dict[str, str] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


class NoToolFoundError(ToolSelectionError):
    """Raised when no tool satisfies the selection criteria.

    This typically means all candidates were eliminated by filters
    (availability, capability, permission, cost, latency, safety,
    user preferences) leaving an empty candidate set.
    """

    def __init__(
        self,
        message: str = "No tool found matching the selection criteria",
        *,
        criteria: dict[str, str] | None = None,
    ) -> None:
        self.criteria = criteria or {}
        details: dict[str, str] = {"criteria": str(self.criteria)}
        super().__init__(message, details=details)


class AmbiguousSelectionError(ToolSelectionError):
    """Raised when multiple tools share the same top ranking score.

    When the engine cannot break a tie automatically the caller must
    decide which tool to use, or adjust weights/filters and retry.
    """

    def __init__(
        self,
        message: str = "Multiple tools tied for the top rank",
        *,
        candidates: list[str] | None = None,
        score: float | None = None,
    ) -> None:
        self.candidates = candidates or []
        self.score = score
        details: dict[str, str] = {
            "candidates": ", ".join(self.candidates),
        }
        if self.score is not None:
            details["score"] = str(self.score)
        super().__init__(message, details=details)


class RankingError(ToolSelectionError):
    """Raised when the ranking computation fails unexpectedly.

    This covers numerical issues (e.g. all weights are zero), missing
    metrics on a tool, or any other error during score calculation.
    """

    def __init__(
        self,
        message: str = "Ranking computation failed",
        *,
        tool_name: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        details: dict[str, str] = {}
        if tool_name is not None:
            details["tool_name"] = tool_name
        super().__init__(message, details=details)


class FilterError(ToolSelectionError):
    """Raised when a filter encounters an unexpected error.

    Individual filters should generally return an empty list rather
    than raising, but this is provided for truly exceptional cases
    (e.g. corrupted tool metadata).
    """

    def __init__(
        self,
        message: str = "Filter execution failed",
        *,
        filter_name: str | None = None,
    ) -> None:
        self.filter_name = filter_name
        details: dict[str, str] = {}
        if filter_name is not None:
            details["filter_name"] = filter_name
        super().__init__(message, details=details)


# ======================================================================
# Execution pipeline exceptions (Sprint 4.3)
# ======================================================================


class ToolExecutionError(Exception):
    """Base exception for all tool-execution errors.

    Every exception raised by the execution pipeline inherits from
    this class, allowing callers to catch any execution-related
    failure with a single ``except ToolExecutionError`` handler.
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> None:
        self.details = details or {}
        super().__init__(message)


class InvalidArgumentsError(ToolExecutionError):
    """Raised when tool arguments fail schema or type validation.

    Attributes:
        field: Name of the invalid field (if applicable).
        expected: Description of what was expected.
        actual: Description of what was received.
    """

    def __init__(
        self,
        message: str = "Invalid tool arguments",
        *,
        field: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        d: dict[str, str] = details or {}
        if field is not None:
            d["field"] = field
        if expected is not None:
            d["expected"] = expected
        if actual is not None:
            d["actual"] = actual
        super().__init__(message, details=d)


class PermissionDeniedError(ToolExecutionError):
    """Raised when the tool's permission level exceeds what is allowed.

    Attributes:
        tool_name: Name of the tool that was denied.
        required_level: The permission level the tool requires.
        max_level: The maximum permission level allowed by the context.
    """

    def __init__(
        self,
        message: str = "Permission denied for tool execution",
        *,
        tool_name: str | None = None,
        required_level: str | None = None,
        max_level: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.required_level = required_level
        self.max_level = max_level
        d: dict[str, str] = details or {}
        if tool_name is not None:
            d["tool_name"] = tool_name
        if required_level is not None:
            d["required_level"] = required_level
        if max_level is not None:
            d["max_level"] = max_level
        super().__init__(message, details=d)


class ExecutionValidationError(ToolExecutionError):
    """Raised when pre-execution validation fails for reasons other
    than arguments or permissions (e.g. missing context fields,
    incompatible execution mode).

    Attributes:
        validation_type: The category of validation that failed.
    """

    def __init__(
        self,
        message: str = "Execution validation failed",
        *,
        validation_type: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.validation_type = validation_type
        d: dict[str, str] = details or {}
        if validation_type is not None:
            d["validation_type"] = validation_type
        super().__init__(message, details=d)


class ExecutionTimeoutError(ToolExecutionError):
    """Raised when a tool execution exceeds its allowed timeout.

    Attributes:
        tool_name: Name of the tool that timed out.
        timeout_ms: The timeout in milliseconds that was exceeded.
    """

    def __init__(
        self,
        message: str = "Tool execution timed out",
        *,
        tool_name: str | None = None,
        timeout_ms: int | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.timeout_ms = timeout_ms
        d: dict[str, str] = details or {}
        if tool_name is not None:
            d["tool_name"] = tool_name
        if timeout_ms is not None:
            d["timeout_ms"] = str(timeout_ms)
        super().__init__(message, details=d)


class ExecutionCancelledError(ToolExecutionError):
    """Raised when a tool execution is cancelled by the user or system.

    Attributes:
        tool_name: Name of the cancelled tool.
        reason: Why the execution was cancelled.
    """

    def __init__(
        self,
        message: str = "Tool execution was cancelled",
        *,
        tool_name: str | None = None,
        reason: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.reason = reason
        d: dict[str, str] = details or {}
        if tool_name is not None:
            d["tool_name"] = tool_name
        if reason is not None:
            d["reason"] = reason
        super().__init__(message, details=d)


__all__ = [
    # Selection errors (Sprint 4.2)
    "AmbiguousSelectionError",
    "FilterError",
    "NoToolFoundError",
    "RankingError",
    "ToolSelectionError",
    # Execution errors (Sprint 4.3)
    "ExecutionCancelledError",
    "ExecutionTimeoutError",
    "ExecutionValidationError",
    "InvalidArgumentsError",
    "PermissionDeniedError",
    "ToolExecutionError",
]
