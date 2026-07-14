"""CollaborationManager — coordinates multi-agent work (Sprint 2.9).

The :class:`CollaborationManager` sits above the
:class:`~communication.bus.AgentMessageBus` and provides higher-level
operations for coordinating multiple agents on a single piece of work:

- :meth:`delegate`           — agent A asks agent B to do a task; returns
                               a :class:`Collaboration` handle.
- :meth:`request_help`       — agent A broadcasts a help request; the
                               first responder wins.
- :meth:`transfer_context`   — agent A sends a ``CONTEXT_TRANSFER`` to
                               agent B.
- :meth:`handoff`            — agent A sends a ``HANDOFF`` to agent B.
- :meth:`wait_for_response`  — block until a ``TASK_RESPONSE`` arrives
                               for a given ``message_id`` (with timeout).
- :meth:`wait_for_completion`— block until a collaboration reaches a
                               terminal state (with timeout).

Each :class:`Collaboration` tracks:

- The initiating message + all related messages (by correlation_id).
- The current :class:`CollaborationStatus`.
- Start / end timestamps.
- Errors (if any).
- A result payload (set when the collaboration completes successfully).

The manager maintains an in-memory registry of active collaborations
and exposes:

- :meth:`get_collaboration` — lookup by id.
- :meth:`list_active`       — list non-terminal collaborations.
- :meth:`history`           — list recent collaborations.
- :meth:`metrics`           — aggregate stats (started/completed/failed/
                              timed_out/avg_duration).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .bus import AgentMessageBus
from .exceptions import CollaborationTimeoutError
from .protocol import AgentMessage, MessagePriority, MessageType

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


class CollaborationStatus(str, Enum):  # noqa: UP042
    """Lifecycle state of a :class:`Collaboration`."""

    INITIATED = "initiated"  # request sent, awaiting response
    IN_PROGRESS = "in_progress"  # response received, work underway
    COMPLETED = "completed"  # finished successfully
    FAILED = "failed"  # finished with error
    TIMED_OUT = "timed_out"  # wait_for_* timed out
    CANCELLED = "cancelled"  # explicitly cancelled


# ----------------------------------------------------------------------
# Collaboration
# ----------------------------------------------------------------------


@dataclass
class Collaboration:
    """A single multi-agent collaboration.

    Attributes:
        id:               Unique collaboration id.
        correlation_id:   Shared correlation id for all related messages.
        initiator:        Agent who started the collaboration.
        participant:      Agent who was asked to participate (None for
                          broadcast help requests).
        initial_message:  The first message sent.
        messages:         All messages in this collaboration (chronological).
        status:           Current :class:`CollaborationStatus`.
        result:           Result payload (set on completion).
        error:            Error message (set on failure).
        started_at:       ISO timestamp.
        completed_at:     ISO timestamp (None until terminal).
        task_id:          Optional task id this collaboration relates to.
    """

    id: str = field(default_factory=lambda: f"collab-{uuid.uuid4().hex[:12]}")
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    initiator: str = ""
    participant: str | None = None
    initial_message: AgentMessage | None = None
    messages: list[AgentMessage] = field(default_factory=list)
    status: CollaborationStatus = CollaborationStatus.INITIATED
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    completed_at: str | None = None
    task_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            CollaborationStatus.COMPLETED,
            CollaborationStatus.FAILED,
            CollaborationStatus.TIMED_OUT,
            CollaborationStatus.CANCELLED,
        }

    @property
    def duration_s(self) -> float:
        """Elapsed seconds since ``started_at`` (or until ``completed_at``)."""
        try:
            start = datetime.fromisoformat(self.started_at)
            end_str = self.completed_at or datetime.now(UTC).isoformat(timespec="seconds")
            end = datetime.fromisoformat(end_str)
            return (end - start).total_seconds()
        except (ValueError, TypeError):
            return 0.0

    def add_message(self, message: AgentMessage) -> None:
        """Append a related message and update status."""
        self.messages.append(message)
        if message.message_type == MessageType.TASK_RESPONSE:
            self.status = CollaborationStatus.IN_PROGRESS
        elif message.message_type == MessageType.ERROR_REPORT:
            self.error = message.payload.get("error", "unknown error")
            self.status = CollaborationStatus.FAILED
            self.completed_at = datetime.now(UTC).isoformat(timespec="seconds")

    def complete(self, result: dict[str, Any] | None = None) -> None:
        """Mark the collaboration as completed."""
        self.result = result
        self.status = CollaborationStatus.COMPLETED
        self.completed_at = datetime.now(UTC).isoformat(timespec="seconds")

    def fail(self, error: str) -> None:
        """Mark the collaboration as failed."""
        self.error = error
        self.status = CollaborationStatus.FAILED
        self.completed_at = datetime.now(UTC).isoformat(timespec="seconds")

    def cancel(self, reason: str = "") -> None:
        """Mark the collaboration as cancelled."""
        self.error = reason or "cancelled"
        self.status = CollaborationStatus.CANCELLED
        self.completed_at = datetime.now(UTC).isoformat(timespec="seconds")

    def timeout(self, what: str = "") -> None:
        """Mark the collaboration as timed out."""
        self.error = f"timed out waiting for {what}" if what else "timed out"
        self.status = CollaborationStatus.TIMED_OUT
        self.completed_at = datetime.now(UTC).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "correlation_id": self.correlation_id,
            "initiator": self.initiator,
            "participant": self.participant,
            "status": self.status.value,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_s": round(self.duration_s, 3),
            "message_count": len(self.messages),
            "result": self.result,
            "error": self.error,
            "initial_message": self.initial_message.serialize() if self.initial_message else None,
        }


# ----------------------------------------------------------------------
# CollaborationManager
# ----------------------------------------------------------------------


class CollaborationManager:
    """Coordinates multi-agent collaborations.

    Usage::

        bus = AgentMessageBus()
        mgr = CollaborationManager(bus=bus)
        collab = await mgr.delegate("planner", "coder", payload={...})
        response = await mgr.wait_for_response(collab.initial_message.message_id, timeout=10)
    """

    def __init__(
        self,
        *,
        bus: AgentMessageBus,
        default_timeout: float = 30.0,
        max_history: int = 1000,
    ) -> None:
        self._bus = bus
        self._default_timeout = default_timeout
        self._max_history = max_history
        # Active collaborations keyed by id.
        self._collaborations: dict[str, Collaboration] = {}
        # Index by correlation_id for fast message routing.
        self._by_correlation: dict[str, Collaboration] = {}
        # Index by initial message_id for wait_for_response.
        self._by_initial_msg: dict[str, Collaboration] = {}
        # Index by task_id.
        self._by_task: dict[str, list[Collaboration]] = {}
        # Futures for pending wait_for_response calls.
        self._response_futures: dict[str, asyncio.Future[AgentMessage]] = {}
        # Metrics.
        self._metrics = {
            "started_total": 0,
            "completed_total": 0,
            "failed_total": 0,
            "timed_out_total": 0,
            "cancelled_total": 0,
            "total_duration_s": 0.0,
        }
        # Auto-subscribe to ALL messages on the bus so responses are
        # routed to collaborations without manual intervention. The
        # subscription is best-effort: if it fails (e.g. no event loop
        # yet), route_message can still be called manually.
        self._auto_subscription_token: str | None = None

    async def _on_bus_message(self, message: AgentMessage) -> None:
        """Callback for the auto-subscription. Forwards to route_message."""
        try:
            await self.route_message(message)
        except Exception:  # noqa: BLE001
            logger.exception("auto route_message failed")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def bus(self) -> AgentMessageBus:
        return self._bus

    @property
    def default_timeout(self) -> float:
        return self._default_timeout

    # ------------------------------------------------------------------
    # Public API — initiate collaborations
    # ------------------------------------------------------------------

    async def delegate(
        self,
        from_agent: str,
        to_agent: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> Collaboration:
        """Agent A delegates a task to agent B.

        Sends a ``TASK_REQUEST`` and returns a :class:`Collaboration`
        handle. Use :meth:`wait_for_response` to await the reply.
        """
        collab = Collaboration(
            initiator=from_agent,
            participant=to_agent,
            task_id=task_id,
        )
        # Ensure auto-subscribe is set up before sending any message.
        await self._ensure_auto_subscribe()
        # Build the message first so initial_message is set before
        # the response can arrive.
        msg = await self._bus.send_task_request(
            from_agent,
            to_agent,
            task_id=task_id,
            payload=payload,
            correlation_id=collab.correlation_id,
            priority=priority,
            ttl=(timeout or self._default_timeout) * 2,  # generous TTL
        )
        collab.initial_message = msg
        collab.messages.append(msg)
        # Register AFTER initial_message is set so _by_initial_msg is
        # populated correctly. By this point the response may already
        # have arrived (if the handler ran synchronously during
        # publish), so route_message may have already been called —
        # but it would have returned early because the collab wasn't
        # registered yet. Re-route any messages that match this
        # collaboration's correlation_id from the bus history.
        await self._register(collab)
        self._replay_history_for(collab)
        return collab

    async def request_help(
        self,
        from_agent: str,
        topic: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Collaboration:
        """Agent A broadcasts a help request.

        The first agent that responds with a ``TASK_RESPONSE`` becomes
        the ``participant`` of the collaboration.
        """
        collab = Collaboration(
            initiator=from_agent,
            participant=None,  # unknown until someone responds
            task_id=task_id,
        )
        body = {"topic": topic}
        if payload:
            body.update(payload)
        await self._ensure_auto_subscribe()
        msg = await self._bus.send_task_request(
            from_agent,
            "*",
            task_id=task_id,
            payload=body,
            correlation_id=collab.correlation_id,
            priority=MessagePriority.HIGH,
            ttl=(timeout or self._default_timeout) * 2,
        )
        collab.initial_message = msg
        collab.messages.append(msg)
        await self._register(collab)
        self._replay_history_for(collab)
        return collab

    async def transfer_context(
        self,
        from_agent: str,
        to_agent: str,
        *,
        context: dict[str, Any],
        task_id: str | None = None,
    ) -> Collaboration:
        """Agent A transfers context to agent B.

        Sends a ``CONTEXT_TRANSFER`` message and returns a
        :class:`Collaboration` handle. This is a one-shot operation
        (no response expected); the collaboration is marked COMPLETED
        immediately.
        """
        collab = Collaboration(
            initiator=from_agent,
            participant=to_agent,
            task_id=task_id,
        )
        await self._ensure_auto_subscribe()
        msg = await self._bus.send_context_transfer(
            from_agent,
            to_agent,
            context=context,
            task_id=task_id,
            correlation_id=collab.correlation_id,
        )
        collab.initial_message = msg
        collab.messages.append(msg)
        collab.complete(result={"transferred": True})
        await self._register(collab)
        self._metrics["completed_total"] += 1
        return collab

    async def handoff(
        self,
        from_agent: str,
        to_agent: str,
        *,
        task_id: str | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Collaboration:
        """Agent A hands off a task to agent B.

        Sends a ``HANDOFF`` message. The collaboration is marked
        COMPLETED once the handoff is acknowledged (or immediately if
        no acknowledgement is expected).
        """
        collab = Collaboration(
            initiator=from_agent,
            participant=to_agent,
            task_id=task_id,
        )
        await self._ensure_auto_subscribe()
        msg = await self._bus.send_handoff(
            from_agent,
            to_agent,
            task_id=task_id,
            reason=reason,
            payload=payload,
            correlation_id=collab.correlation_id,
        )
        collab.initial_message = msg
        collab.messages.append(msg)
        await self._register(collab)
        self._replay_history_for(collab)
        return collab

    # ------------------------------------------------------------------
    # Public API — wait for responses
    # ------------------------------------------------------------------

    async def wait_for_response(
        self,
        message_id: str,
        *,
        timeout: float | None = None,
    ) -> AgentMessage:
        """Block until a ``TASK_RESPONSE`` for ``message_id`` arrives.

        Args:
            message_id: The ``in_reply_to`` of the expected response.
                        This is typically ``collab.initial_message.message_id``.
            timeout:    Seconds to wait (default: ``default_timeout``).

        Raises:
            CollaborationTimeoutError: if no response arrives in time.
        """
        timeout = timeout if timeout is not None else self._default_timeout
        # Check if a response has already arrived.
        collab = self._find_by_initial_msg(message_id)
        if collab is not None:
            for m in collab.messages:
                if m.in_reply_to == message_id and m.message_type == MessageType.TASK_RESPONSE:
                    return m
        # Register a future.
        future: asyncio.Future[AgentMessage] = asyncio.get_event_loop().create_future()
        self._response_futures[message_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            if collab is not None:
                collab.timeout("response")
                self._metrics["timed_out_total"] += 1
            raise CollaborationTimeoutError(
                message_id,
                waited_s=timeout,
                what="response",
            ) from exc
        finally:
            self._response_futures.pop(message_id, None)

    async def wait_for_completion(
        self,
        collaboration_id: str,
        *,
        timeout: float | None = None,
    ) -> Collaboration:
        """Block until ``collaboration_id`` reaches a terminal state.

        Polls every 50ms (default) up to ``timeout`` seconds.

        Raises:
            CollaborationTimeoutError: if the collaboration doesn't
                complete in time.
            KeyError: if the collaboration doesn't exist.
        """
        timeout = timeout if timeout is not None else self._default_timeout
        collab = self._collaborations.get(collaboration_id)
        if collab is None:
            raise KeyError(collaboration_id)
        deadline = asyncio.get_event_loop().time() + timeout
        while not collab.is_terminal:
            if asyncio.get_event_loop().time() >= deadline:
                collab.timeout("completion")
                self._metrics["timed_out_total"] += 1
                raise CollaborationTimeoutError(
                    collaboration_id,
                    waited_s=timeout,
                    what="completion",
                )
            await asyncio.sleep(0.05)
        return collab

    # ------------------------------------------------------------------
    # Public API — message routing
    # ------------------------------------------------------------------

    async def route_message(self, message: AgentMessage) -> None:
        """Route an incoming message to its collaboration (if any).

        This is the hook the bus calls for every published message.
        It updates the collaboration's status, appends the message,
        and resolves any pending ``wait_for_response`` futures.
        """
        # Find by correlation_id.
        collab = self._by_correlation.get(message.correlation_id or "")
        if collab is None and message.in_reply_to:
            # Try by initial message id.
            collab = self._find_by_initial_msg(message.in_reply_to)
        if collab is None:
            return  # not part of any tracked collaboration
        collab.add_message(message)
        # If this is a response, resolve the future + maybe complete.
        if message.message_type == MessageType.TASK_RESPONSE:
            initial_id = collab.initial_message.message_id if collab.initial_message else ""
            fut = self._response_futures.get(initial_id)
            if fut and not fut.done():
                fut.set_result(message)
            # If participant was unknown (help request), record it.
            if collab.participant is None:
                collab.participant = message.sender_agent
            # If the response contains a "done" flag, complete.
            if message.payload.get("done"):
                collab.complete(result=message.payload)
                self._metrics["completed_total"] += 1
                self._metrics["total_duration_s"] += collab.duration_s
        elif message.message_type == MessageType.ERROR_REPORT:
            if collab.initial_message:
                initial_id = collab.initial_message.message_id
                fut = self._response_futures.get(initial_id)
                if fut and not fut.done():
                    fut.set_exception(
                        CollaborationTimeoutError(
                            collab.id,
                            waited_s=0.0,
                            what=f"error: {message.payload.get('error', '')}",
                        )
                    )
            self._metrics["failed_total"] += 1
            self._metrics["total_duration_s"] += collab.duration_s
        elif message.message_type == MessageType.STATUS_UPDATE:
            # Just record; don't change status.
            pass

    # ------------------------------------------------------------------
    # Public API — queries
    # ------------------------------------------------------------------

    def get_collaboration(self, collaboration_id: str) -> Collaboration | None:
        return self._collaborations.get(collaboration_id)

    def list_active(self) -> list[Collaboration]:
        return [c for c in self._collaborations.values() if not c.is_terminal]

    def list_by_initiator(self, initiator: str) -> list[Collaboration]:
        return [c for c in self._collaborations.values() if c.initiator == initiator]

    def list_by_participant(self, participant: str) -> list[Collaboration]:
        return [c for c in self._collaborations.values() if c.participant == participant]

    def list_by_task(self, task_id: str) -> list[Collaboration]:
        return list(self._by_task.get(task_id, []))

    def history(self, *, limit: int = 100) -> list[Collaboration]:
        """Return recent collaborations (newest first)."""
        return list(reversed(list(self._collaborations.values())))[:limit]

    # ------------------------------------------------------------------
    # Public API — control
    # ------------------------------------------------------------------

    def cancel(self, collaboration_id: str, reason: str = "") -> bool:
        """Cancel a collaboration. Returns True if found."""
        collab = self._collaborations.get(collaboration_id)
        if collab is None or collab.is_terminal:
            return False
        collab.cancel(reason)
        self._metrics["cancelled_total"] += 1
        self._metrics["total_duration_s"] += collab.duration_s
        return True

    def complete(self, collaboration_id: str, result: dict[str, Any] | None = None) -> bool:
        """Manually mark a collaboration as completed. Returns True if found."""
        collab = self._collaborations.get(collaboration_id)
        if collab is None or collab.is_terminal:
            return False
        collab.complete(result)
        self._metrics["completed_total"] += 1
        self._metrics["total_duration_s"] += collab.duration_s
        return True

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        return {
            **self._metrics,
            "active_count": len(self.list_active()),
            "total_count": len(self._collaborations),
            "avg_duration_s": (
                round(
                    self._metrics["total_duration_s"]
                    / max(
                        1,
                        (
                            self._metrics["completed_total"]
                            + self._metrics["failed_total"]
                            + self._metrics["timed_out_total"]
                            + self._metrics["cancelled_total"]
                        ),
                    ),
                    3,
                )
            ),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _register(self, collab: Collaboration) -> None:
        # Ensure auto-subscribe is set up so responses get routed.
        await self._ensure_auto_subscribe()
        self._collaborations[collab.id] = collab
        if collab.correlation_id:
            self._by_correlation[collab.correlation_id] = collab
        if collab.initial_message:
            self._by_initial_msg[collab.initial_message.message_id] = collab
        if collab.task_id:
            self._by_task.setdefault(collab.task_id, []).append(collab)
        self._metrics["started_total"] += 1
        # Trim history if over capacity.
        if len(self._collaborations) > self._max_history:
            # Remove oldest terminal collaborations.
            terminal = sorted(
                [c for c in self._collaborations.values() if c.is_terminal],
                key=lambda c: c.completed_at or c.started_at,
            )
            for c in terminal[: len(self._collaborations) - self._max_history]:
                self._unregister(c)

    async def _ensure_auto_subscribe(self) -> None:
        """Ensure the auto-subscription to the bus is active."""
        if self._auto_subscription_token is not None:
            return
        try:
            self._auto_subscription_token = await self._bus.subscribe_broadcast(
                self._on_bus_message
            )
        except Exception:  # noqa: BLE001
            logger.debug("auto-subscribe failed; route_message must be called manually")

    def _replay_history_for(self, collab: Collaboration) -> None:
        """Re-play any bus messages that match ``collab.correlation_id``.

        When a message is published and the handler responds
        synchronously during the same ``publish`` call, the response
        arrives BEFORE the collaboration is registered. This method
        scans the bus history for messages with the same correlation_id
        and re-routes them to the collaboration.
        """
        if not collab.correlation_id:
            return
        for msg in self._bus.history():
            if msg.correlation_id == collab.correlation_id and msg not in collab.messages:
                collab.add_message(msg)
                # Set participant if this is a response and participant
                # is unknown (e.g. for broadcast help requests).
                if (
                    msg.message_type == MessageType.TASK_RESPONSE
                    and collab.participant is None
                    and msg.sender_agent
                ):
                    collab.participant = msg.sender_agent
                # Resolve any pending wait_for_response futures.
                if msg.message_type == MessageType.TASK_RESPONSE and collab.initial_message:
                    fut = self._response_futures.get(collab.initial_message.message_id)
                    if fut and not fut.done():
                        fut.set_result(msg)
                    # If the response signals completion, mark the collab.
                    if msg.payload.get("done") and not collab.is_terminal:
                        collab.complete(result=msg.payload)
                        self._metrics["completed_total"] += 1
                        self._metrics["total_duration_s"] += collab.duration_s

    def _unregister(self, collab: Collaboration) -> None:
        self._collaborations.pop(collab.id, None)
        if collab.correlation_id:
            self._by_correlation.pop(collab.correlation_id, None)
        if collab.initial_message:
            self._by_initial_msg.pop(collab.initial_message.message_id, None)

    def _find_by_initial_msg(self, message_id: str) -> Collaboration | None:
        return self._by_initial_msg.get(message_id)


__all__ = [
    "Collaboration",
    "CollaborationManager",
    "CollaborationStatus",
]
