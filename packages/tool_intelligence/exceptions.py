"""Exceptions for the Tool Intelligence package.

Sprint 4.1 — Tool Intelligence Foundation.

These exceptions form the error vocabulary used across the package:
  - ``ToolNotCompatible`` — compatibility checker rejects a tool.
  - ``CapabilityMissing``  — a required capability is not satisfied.
  - ``ToolSelectionError``  — selector cannot produce candidates.
  - ``RankingError``        — ranker fails to score candidates.

All exceptions inherit from ``ToolIntelligenceError`` so callers can
catch the whole family with a single ``except`` clause when needed.
"""

from __future__ import annotations

from typing import Any


class ToolIntelligenceError(Exception):
    """Base class for all Tool Intelligence errors."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation of the error."""
        return {
            "error": type(self).__name__,
            "message": self.message,
            "details": dict(self.details),
        }


class ToolNotCompatible(ToolIntelligenceError):
    """Raised when a tool is not compatible with the execution environment.

    Carries structured information about which compatibility check
    failed (os, dependencies, plugins, permissions, model required).
    """


class CapabilityMissing(ToolIntelligenceError):
    """Raised when a required capability is not provided by any tool.

    Attributes:
        required_capability: The capability that was requested.
        available_tools:     Names of the tools that were considered.
    """

    def __init__(
        self,
        required_capability: str,
        *,
        available_tools: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        self.required_capability = required_capability
        self.available_tools = available_tools or []
        msg = message or (
            f"No tool provides required capability '{required_capability}'. "
            f"Considered tools: {self.available_tools or 'none'}"
        )
        super().__init__(
            msg,
            details={
                "required_capability": required_capability,
                "available_tools": list(self.available_tools),
            },
        )


class ToolSelectionError(ToolIntelligenceError):
    """Raised when the ToolSelector cannot produce a valid candidate list.

    This is a hard failure: the selector was asked to do its job but
    could not even produce an empty candidate list (e.g. bad input,
    registry unavailable).
    """


class RankingError(ToolIntelligenceError):
    """Raised when the ToolRanker fails to score candidates.

    This usually indicates an internal bug (bad weights, malformed
    metadata, division by zero) rather than a missing tool.
    """


__all__ = [
    "CapabilityMissing",
    "RankingError",
    "ToolIntelligenceError",
    "ToolNotCompatible",
    "ToolSelectionError",
]
