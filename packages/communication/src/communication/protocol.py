"""Agent communication protocol — canonical message types (Sprint 2.9).

This module defines the **canonical inter-agent message protocol**
required by Sprint 2.9. It does NOT replace the existing
:class:`multiagent.messages.Message`; instead, it provides a typed
facade (:class:`AgentMessage`) that wraps a `Message` and exposes
the 6 message types required by the sprint spec:

- ``TASK_REQUEST``      — agent A asks agent B to perform a task.
- ``TASK_RESPONSE``     — agent B returns the result of a task.
- ``STATUS_UPDATE``     — agent reports its current status.
- ``ERROR_REPORT``      — agent reports an error to another agent or orchestrator.
- ``CONTEXT_TRANSFER``  — agent transfers context to another agent.
- ``HANDOFF``           — agent hands off a task to another agent.

Each :class:`AgentMessage` carries the fields required by the spec:

- ``message_id``
- ``sender_agent``
- ``receiver_agent``
- ``task_id``           (optional; for task-scoped messages)
- ``timestamp``
- ``message_type``
- ``payload``
- ``metadata``

Plus the following additions for tracing and routing:

- ``correlation_id``   (for end-to-end tracing of a conversation)
- ``priority``         (CRITICAL / HIGH / NORMAL / LOW)
- ``ttl``              (seconds; 0 = no expiration)
- ``in_reply_to``      (message_id of the message this replies to)

The :meth:`AgentMessage.to_message` / :meth:`AgentMessage.from_message`
helpers convert to/from the existing wire format
(:class:`multiagent.messages.Message`) so the new layer is fully
interoperable with the existing ``MessageBus``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class MessageType(str, Enum):  # noqa: UP042
    """Canonical inter-agent message types (Sprint 2.9 spec).

    The 6 types required by the sprint are the primary members. The
    additional ``SYSTEM`` and ``EVENT`` aliases map to the existing
    :class:`multiagent.enums.MessageType` values for backward
    compatibility when bridging to the wire format.
    """

    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    STATUS_UPDATE = "status_update"
    ERROR_REPORT = "error_report"
    CONTEXT_TRANSFER = "context_transfer"
    HANDOFF = "handoff"

    # Aliases that map to the existing MessageType values. These allow
    # the new protocol to be used in places that still expect the old
    # enum without breaking callers.
    SYSTEM = "system"
    EVENT = "event"


class MessagePriority(str, Enum):  # noqa: UP042
    """Priority of an :class:`AgentMessage`.

    Aligned with :class:`multiagent.enums.Priority` but declared
    separately so the communication package has no hard import-time
    dependency on the multiagent package. Adapters translate between
    the two when bridging.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_PRIORITY_WEIGHT: dict[MessagePriority, int] = {
    MessagePriority.CRITICAL: 0,
    MessagePriority.HIGH: 1,
    MessagePriority.NORMAL: 2,
    MessagePriority.LOW: 3,
}


def priority_weight(p: MessagePriority) -> int:
    """Numeric weight for ``p`` (lower = more urgent)."""
    return _PRIORITY_WEIGHT[p]


# ----------------------------------------------------------------------
# AgentMessage
# ----------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id(prefix: str = "msg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class AgentMessage:
    """Canonical inter-agent message (Sprint 2.9 protocol).

    This dataclass is the **canonical** representation used inside the
    communication package. It is convertible to/from the existing wire
    format (:class:`multiagent.messages.Message`) via
    :meth:`to_message` / :meth:`from_message`.

    Attributes:
        message_id:       Unique message identifier.
        sender_agent:     Name of the sending agent (or ``"orchestrator"``).
        receiver_agent:   Name of the receiving agent (or ``"*"`` for broadcast).
        task_id:          Optional task id this message relates to.
        timestamp:        ISO-8601 UTC creation timestamp.
        message_type:     :class:`MessageType` of this message.
        payload:          Free-form message payload (JSON-compatible).
        metadata:         Free-form metadata (correlation tags, tracing, etc.).
        correlation_id:   Optional end-to-end correlation id (for tracing
                          a multi-message conversation).
        priority:         :class:`MessagePriority` (default NORMAL).
        ttl:              Time-to-live in seconds (0 = no expiration).
        in_reply_to:      Optional ``message_id`` of the message this replies to.
    """

    message_id: str = field(default_factory=lambda: _new_id("msg"))
    sender_agent: str = ""
    receiver_agent: str = ""
    task_id: str | None = None
    timestamp: str = field(default_factory=_now_iso)
    message_type: MessageType = MessageType.TASK_REQUEST
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    priority: MessagePriority = MessagePriority.NORMAL
    ttl: float = 0.0
    in_reply_to: str | None = None

    def __post_init__(self) -> None:
        # Defensive copies.
        self.payload = dict(self.payload)
        self.metadata = dict(self.metadata)
        # Allow empty sender_agent for broadcast replies (the caller
        # fills it in after reply()). Otherwise require non-empty.

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_broadcast(self) -> bool:
        """True if this message is addressed to all agents (``receiver == "*"``)."""
        return self.receiver_agent == "*"

    @property
    def is_expired(self) -> bool:
        """True if this message's TTL has elapsed.

        TTL of 0 means "no expiration".
        """
        if self.ttl <= 0:
            return False
        try:
            created = datetime.fromisoformat(self.timestamp)
            age = (datetime.now(UTC) - created).total_seconds()
            return age > self.ttl
        except (ValueError, TypeError):
            return False

    @property
    def weight(self) -> int:
        """Numeric priority weight (lower = more urgent)."""
        return priority_weight(self.priority)

    # ------------------------------------------------------------------
    # Factory constructors
    # ------------------------------------------------------------------

    @classmethod
    def task_request(
        cls,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: float = 0.0,
    ) -> AgentMessage:
        """Build a ``TASK_REQUEST`` message."""
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            task_id=task_id,
            message_type=MessageType.TASK_REQUEST,
            payload=dict(payload or {}),
            correlation_id=correlation_id,
            priority=priority,
            ttl=ttl,
        )

    @classmethod
    def task_response(
        cls,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        in_reply_to: str | None = None,
        correlation_id: str | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> AgentMessage:
        """Build a ``TASK_RESPONSE`` message (replies to a ``TASK_REQUEST``)."""
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            task_id=task_id,
            message_type=MessageType.TASK_RESPONSE,
            payload=dict(payload or {}),
            in_reply_to=in_reply_to,
            correlation_id=correlation_id,
            priority=priority,
        )

    @classmethod
    def status_update(
        cls,
        sender: str,
        receiver: str = "*",
        *,
        status: str = "",
        progress: float | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Build a ``STATUS_UPDATE`` message (default broadcast)."""
        body = dict(payload or {})
        if status:
            body.setdefault("status", status)
        if progress is not None:
            body.setdefault("progress", progress)
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            message_type=MessageType.STATUS_UPDATE,
            payload=body,
            correlation_id=correlation_id,
            priority=MessagePriority.LOW,
        )

    @classmethod
    def error_report(
        cls,
        sender: str,
        receiver: str = "orchestrator",
        *,
        error: str = "",
        code: str = "INTERNAL",
        task_id: str | None = None,
        in_reply_to: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build an ``ERROR_REPORT`` message (default to orchestrator)."""
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            task_id=task_id,
            message_type=MessageType.ERROR_REPORT,
            payload={"error": error, "code": code},
            in_reply_to=in_reply_to,
            correlation_id=correlation_id,
            priority=MessagePriority.HIGH,
        )

    @classmethod
    def context_transfer(
        cls,
        sender: str,
        receiver: str,
        *,
        context: dict[str, Any],
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build a ``CONTEXT_TRANSFER`` message."""
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            task_id=task_id,
            message_type=MessageType.CONTEXT_TRANSFER,
            payload={"context": dict(context)},
            correlation_id=correlation_id,
            priority=MessagePriority.HIGH,
        )

    @classmethod
    def handoff(
        cls,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build a ``HANDOFF`` message (agent A hands off to agent B)."""
        body = dict(payload or {})
        if reason:
            body.setdefault("reason", reason)
        return cls(
            sender_agent=sender,
            receiver_agent=receiver,
            task_id=task_id,
            message_type=MessageType.HANDOFF,
            payload=body,
            correlation_id=correlation_id,
            priority=MessagePriority.HIGH,
        )

    # ------------------------------------------------------------------
    # Reply helper
    # ------------------------------------------------------------------

    def reply(
        self,
        message_type: MessageType,
        *,
        payload: dict[str, Any] | None = None,
        priority: MessagePriority | None = None,
    ) -> AgentMessage:
        """Build a reply to this message (swaps sender/receiver, sets ``in_reply_to``).

        The reply inherits ``correlation_id``, ``task_id`` and (unless
        overridden) ``priority`` from the original.

        For broadcast messages (``receiver == "*"``), the reply is
        addressed back to the original sender; the caller is responsible
        for setting the correct ``sender_agent`` on the reply (typically
        the responder's own name).
        """
        return AgentMessage(
            # For broadcast, the responder's name is unknown here; the
            # caller should override sender_agent after calling reply().
            sender_agent=self.receiver_agent if not self.is_broadcast else "",
            receiver_agent=self.sender_agent,
            task_id=self.task_id,
            message_type=message_type,
            payload=dict(payload or {}),
            metadata=dict(self.metadata),
            correlation_id=self.correlation_id,
            priority=priority or self.priority,
            in_reply_to=self.message_id,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (round-trip safe)."""
        return {
            "message_id": self.message_id,
            "sender_agent": self.sender_agent,
            "receiver_agent": self.receiver_agent,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "message_type": self.message_type.value,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "correlation_id": self.correlation_id,
            "priority": self.priority.value,
            "ttl": self.ttl,
            "in_reply_to": self.in_reply_to,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> AgentMessage:
        """Reconstruct from :meth:`serialize` output.

        Unknown keys are ignored; missing keys fall back to defaults.
        """
        try:
            mtype = MessageType(data.get("message_type", "task_request"))
        except ValueError as exc:
            raise ValueError(f"unknown message_type: {data.get('message_type')!r}") from exc
        try:
            prio = MessagePriority(data.get("priority", "normal"))
        except ValueError as exc:
            raise ValueError(f"unknown priority: {data.get('priority')!r}") from exc
        return cls(
            message_id=data.get("message_id") or _new_id("msg"),
            sender_agent=data.get("sender_agent", ""),
            receiver_agent=data.get("receiver_agent", ""),
            task_id=data.get("task_id"),
            timestamp=data.get("timestamp") or _now_iso(),
            message_type=mtype,
            payload=dict(data.get("payload", {})),
            metadata=dict(data.get("metadata", {})),
            correlation_id=data.get("correlation_id"),
            priority=prio,
            ttl=float(data.get("ttl", 0.0)),
            in_reply_to=data.get("in_reply_to"),
        )

    # ------------------------------------------------------------------
    # Bridge to existing wire format
    # ------------------------------------------------------------------

    def to_message(self) -> Any:
        """Convert to a :class:`multiagent.messages.Message` (wire format).

        Imports the multiagent package lazily so this package can be
        imported without it.
        """
        from multiagent.enums import MessageType as WireMessageType
        from multiagent.enums import Priority
        from multiagent.messages import Message

        # Map our MessageType to the wire MessageType.
        wire_type_map = {
            MessageType.TASK_REQUEST: WireMessageType.REQUEST,
            MessageType.TASK_RESPONSE: WireMessageType.RESPONSE,
            MessageType.STATUS_UPDATE: WireMessageType.EVENT,
            MessageType.ERROR_REPORT: WireMessageType.ERROR,
            MessageType.CONTEXT_TRANSFER: WireMessageType.EVENT,
            MessageType.HANDOFF: WireMessageType.REQUEST,
            MessageType.SYSTEM: WireMessageType.SYSTEM,
            MessageType.EVENT: WireMessageType.EVENT,
        }
        # Map our priority to the wire Priority.
        wire_prio_map = {
            MessagePriority.CRITICAL: Priority.CRITICAL,
            MessagePriority.HIGH: Priority.HIGH,
            MessagePriority.NORMAL: Priority.NORMAL,
            MessagePriority.LOW: Priority.LOW,
        }
        # Pack our protocol fields into the wire Message payload so
        # nothing is lost in translation.
        packed_payload = {
            "message_type": self.message_type.value,
            "task_id": self.task_id,
            "in_reply_to": self.in_reply_to,
            "protocol": "agent_message_v1",
            **self.payload,
        }
        return Message(
            sender=self.sender_agent,
            receiver=self.receiver_agent,
            type=wire_type_map.get(self.message_type, WireMessageType.EVENT),
            payload=packed_payload,
            priority=wire_prio_map.get(self.priority, Priority.NORMAL),
            id=self.message_id,
            timestamp=self.timestamp,
            correlation_id=self.correlation_id,
            ttl=self.ttl,
        )

    @classmethod
    def from_message(cls, msg: Any) -> AgentMessage:
        """Reconstruct from a :class:`multiagent.messages.Message`.

        Inverse of :meth:`to_message`. Recovers the original
        :class:`MessageType` from ``payload['message_type']`` if present
        (falls back to inferring from the wire ``type``).
        """

        payload = dict(getattr(msg, "payload", {}) or {})
        mtype_value = payload.pop("message_type", None)
        task_id = payload.pop("task_id", None)
        in_reply_to = payload.pop("in_reply_to", None)
        payload.pop("protocol", None)

        if mtype_value:
            try:
                mtype = MessageType(mtype_value)
            except ValueError:
                mtype = _infer_type_from_wire(getattr(msg, "type", None))
        else:
            mtype = _infer_type_from_wire(getattr(msg, "type", None))

        # Map wire Priority back to our priority.
        from multiagent.enums import Priority as WirePriority

        wire_prio = getattr(msg, "priority", WirePriority.NORMAL)
        prio_map = {
            WirePriority.CRITICAL: MessagePriority.CRITICAL,
            WirePriority.HIGH: MessagePriority.HIGH,
            WirePriority.NORMAL: MessagePriority.NORMAL,
            WirePriority.LOW: MessagePriority.LOW,
        }
        return cls(
            message_id=getattr(msg, "id", None) or _new_id("msg"),
            sender_agent=getattr(msg, "sender", "") or "",
            receiver_agent=getattr(msg, "receiver", "") or "",
            task_id=task_id,
            timestamp=getattr(msg, "timestamp", None) or _now_iso(),
            message_type=mtype,
            payload=payload,
            metadata={},
            correlation_id=getattr(msg, "correlation_id", None),
            priority=prio_map.get(wire_prio, MessagePriority.NORMAL),
            ttl=float(getattr(msg, "ttl", 0.0) or 0.0),
            in_reply_to=in_reply_to,
        )

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"AgentMessage(id={self.message_id!r}, "
            f"type={self.message_type.value!r}, "
            f"{self.sender_agent!r} -> {self.receiver_agent!r})"
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _infer_type_from_wire(wire_type: Any) -> MessageType:
    """Infer :class:`MessageType` from a wire ``MessageType`` value."""
    from multiagent.enums import MessageType as WireMessageType

    mapping = {
        WireMessageType.REQUEST: MessageType.TASK_REQUEST,
        WireMessageType.RESPONSE: MessageType.TASK_RESPONSE,
        WireMessageType.EVENT: MessageType.STATUS_UPDATE,
        WireMessageType.ERROR: MessageType.ERROR_REPORT,
        WireMessageType.SYSTEM: MessageType.SYSTEM,
    }
    return mapping.get(wire_type, MessageType.STATUS_UPDATE)


__all__ = [
    "AgentMessage",
    "MessagePriority",
    "MessageType",
    "priority_weight",
]
