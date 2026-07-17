"""Custom exceptions for the Tool Selection Engine.

Sprint 4.2 — Tool Intelligence: Tool Selection Engine.

Provides a structured exception hierarchy for selection, ranking,
and filtering errors so callers can distinguish between different
failure modes programmatically.
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


__all__ = [
    "AmbiguousSelectionError",
    "FilterError",
    "NoToolFoundError",
    "RankingError",
    "ToolSelectionError",
]
