"""Exception hierarchy for the communication package (Sprint 2.9).

All communication exceptions inherit from :class:`CommunicationError`
so callers can catch any comms-related failure with a single
``except`` clause.

Hierarchy::

    CommunicationError
    ├── MessageDeliveryError        (publish/subscribe failed)
    ├── MessageExpiredError         (TTL exceeded)
    ├── AgentNotFoundError          (receiver unknown)
    ├── CollaborationError          (collaboration-level fault)
    ├── CollaborationTimeoutError   (wait_for_response / wait_for_completion timed out)
    └── PersistenceError            (message repository write failed)
"""

from __future__ import annotations

from typing import Any


class CommunicationError(Exception):
    """Base class for every communication-related exception.

    Attributes:
        message: Human-readable description.
        details: Optional structured payload.
    """

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "details": dict(self.details),
        }

    def __str__(self) -> str:
        if not self.message:
            return type(self).__name__
        if self.details:
            return f"{self.message} :: {self.details}"
        return self.message


class MessageDeliveryError(CommunicationError):
    """Raised when a message cannot be delivered."""

    def __init__(self, message_id: str, reason: str = "") -> None:
        super().__init__(
            reason or f"failed to deliver message '{message_id}'",
            details={"message_id": message_id},
        )
        self.message_id = message_id


class MessageExpiredError(CommunicationError):
    """Raised when a message's TTL has been exceeded."""

    def __init__(self, message_id: str, age: float, ttl: float) -> None:
        super().__init__(
            f"message '{message_id}' expired (age={age:.1f}s, ttl={ttl:.1f}s)",
            details={"message_id": message_id, "age": age, "ttl": ttl},
        )
        self.message_id = message_id


class AgentNotFoundError(CommunicationError):
    """Raised when a receiver agent is not registered."""

    def __init__(self, agent_name: str) -> None:
        super().__init__(
            f"agent '{agent_name}' not found",
            details={"agent_name": agent_name},
        )
        self.agent_name = agent_name


class CollaborationError(CommunicationError):
    """Generic collaboration-level fault."""


class CollaborationTimeoutError(CollaborationError):
    """Raised when a collaboration wait times out."""

    def __init__(self, collaboration_id: str, *, waited_s: float, what: str = "") -> None:
        super().__init__(
            f"collaboration '{collaboration_id}' timed out after {waited_s:.1f}s"
            + (f" waiting for {what}" if what else ""),
            details={
                "collaboration_id": collaboration_id,
                "waited_s": waited_s,
                "what": what,
            },
        )
        self.collaboration_id = collaboration_id
        self.waited_s = waited_s


class PersistenceError(CommunicationError):
    """Raised when persisting a message fails."""


__all__ = [
    "AgentNotFoundError",
    "CollaborationError",
    "CollaborationTimeoutError",
    "CommunicationError",
    "MessageDeliveryError",
    "MessageExpiredError",
    "PersistenceError",
]
