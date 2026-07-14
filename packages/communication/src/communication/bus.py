"""AgentMessageBus — facade over the existing MessageBus (Sprint 2.9).

The :class:`AgentMessageBus` is the **canonical** message bus exposed
by the communication package. It wraps an existing
:class:`multiagent.messages.MessageBus` (or stands alone with an
internal in-memory bus) and adds:

- **Persistence**: every published :class:`AgentMessage` is written to
  a :class:`~communication.repository.IMessageRepository` (default:
  in-memory; can be wired to SQLite via
  :class:`~communication.repository.MessageRepository`).
- **TTL enforcement**: messages whose ``ttl`` has elapsed are dropped
  at publish time (the existing ``MessageBus`` does not enforce TTL).
- **Tracing**: every message is tagged with a ``correlation_id`` and
  the bus exposes :meth:`history` for querying past messages by
  correlation_id / sender / receiver / type.
- **Metrics**: published / delivered / dropped / expired counters.
- **Bridge to EventBus**: optionally republish each message as a
  ``workflow.EventBus`` ``Event`` for unified observability.

The bus is **asyncio-safe** and supports both point-to-point and
broadcast messaging. It does NOT replace the underlying
``MessageBus`` — it composes it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .exceptions import AgentNotFoundError, MessageDeliveryError
from .protocol import AgentMessage, MessagePriority, MessageType
from .repository import IMessageRepository, InMemoryMessageRepository

logger = logging.getLogger(__name__)

# Type alias for handlers.
MessageHandler = Callable[[AgentMessage], Awaitable[None]]


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------


@dataclass
class BusConfig:
    """Configuration for :class:`AgentMessageBus`.

    Attributes:
        enforce_ttl:        If True, expired messages are dropped at
                            publish time (default True).
        persist_messages:   If True, every published message is written
                            to the repository (default True).
        bridge_to_event_bus: If True, every published message is also
                            republished as an Event on the (optional)
                            workflow EventBus (default False).
        default_ttl:        Default TTL when the message doesn't set
                            one (0 = no expiration).
        max_history:        Max messages kept in the in-bus history
                            ring buffer (separate from the repository).
    """

    enforce_ttl: bool = True
    persist_messages: bool = True
    bridge_to_event_bus: bool = False
    default_ttl: float = 0.0
    max_history: int = 1000


# ----------------------------------------------------------------------
# AgentMessageBus
# ----------------------------------------------------------------------


class AgentMessageBus:
    """Facade over :class:`multiagent.messages.MessageBus`.

    Can be constructed in three modes:

    1. **Standalone** (no underlying bus): use an internal asyncio
       dispatcher. Suitable for tests and lightweight usage.
    2. **Wrapping** an existing ``MessageBus``: pass it as ``bus=``.
       All publish/subscribe calls are forwarded.
    3. **Integrated**: also pass a ``repository`` for persistence and
       an ``event_bus`` for observability bridging.

    Usage::

        bus = AgentMessageBus()
        await bus.subscribe("coder", handler)
        msg = AgentMessage.task_request("planner", "coder", payload={...})
        await bus.publish(msg)
    """

    def __init__(
        self,
        *,
        bus: Any | None = None,
        repository: IMessageRepository | None = None,
        event_bus: Any | None = None,
        config: BusConfig | None = None,
    ) -> None:
        self._config = config or BusConfig()
        self._repository = repository or InMemoryMessageRepository()
        self._event_bus = event_bus
        # Lazily import and create the underlying bus if not provided.
        self._bus = bus if bus is not None else _create_default_bus()
        # Subscriber registry (for standalone mode + metrics).
        self._subscribers: dict[str, dict[str, MessageHandler]] = {}
        self._broadcast_subscribers: dict[str, MessageHandler] = {}
        # Map wrapper token -> underlying bus token (for unsubscribe).
        self._token_map: dict[str, str] = {}
        # History ring buffer.
        self._history: list[AgentMessage] = []
        # Metrics (typed as dict[str, Any] to allow mixed int/dict values).
        self._metrics: dict[str, Any] = {
            "published_total": 0,
            "delivered_total": 0,
            "dropped_expired": 0,
            "dropped_no_subscriber": 0,
            "persist_errors": 0,
            "by_type": {t.value: 0 for t in MessageType},
        }
        # Lock for atomic history updates.
        self._lock = asyncio.Lock()
        # Wire up the underlying bus: subscribe to ALL messages on the
        # underlying bus so we can persist + bridge them. Defer to first
        # publish/subscribe call so we're inside an event loop.
        self._tap_installed = False

    async def _ensure_tap(self) -> None:
        """Ensure the broadcast tap is installed on the underlying bus.

        The tap is ONLY installed when wrapping a real MessageBus (not
        the standalone ``_StandaloneBus``). In standalone mode,
        :meth:`publish` handles persistence/history/dispatch directly.
        """
        if self._tap_installed:
            return
        self._tap_installed = True
        if isinstance(self._bus, _StandaloneBus):
            return  # no tap needed for standalone mode
        await self._install_tap()

    async def _install_tap(self) -> None:
        """Install the broadcast tap on the underlying bus.

        The tap captures messages published via the raw underlying bus
        API (bypassing :meth:`publish`) so they are still persisted and
        recorded in history.
        """

        async def _tap(wire_msg: Any) -> None:
            try:
                if isinstance(wire_msg, AgentMessage):
                    msg = wire_msg
                else:
                    msg = AgentMessage.from_message(wire_msg)
                # Check if this message was already processed by publish().
                # We use the message_id as a deduplication key.
                if any(m.message_id == msg.message_id for m in self._history):
                    return  # already processed by publish()
                # This message came in via the raw bus — process it now.
                if self._config.persist_messages:
                    try:
                        self._repository.save(msg)
                    except Exception:  # noqa: BLE001
                        logger.exception("tap: failed to persist message")
                async with self._lock:
                    self._history.append(msg)
                    if len(self._history) > self._config.max_history:
                        self._history = self._history[-self._config.max_history :]
                    self._metrics["published_total"] += 1
                    self._metrics["by_type"][msg.message_type.value] = (
                        self._metrics["by_type"].get(msg.message_type.value, 0) + 1
                    )
                if self._config.bridge_to_event_bus and self._event_bus is not None:
                    try:
                        await self._event_bus.publish(
                            f"agent.message.{msg.message_type.value}",
                            source=msg.sender_agent,
                            payload=msg.serialize(),
                            correlation_id=msg.correlation_id,
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("tap: failed to bridge to event bus")
            except Exception:  # noqa: BLE001
                logger.exception("tap: failed to process wire message")

        try:
            await self._bus.subscribe_broadcast(_tap)
        except Exception:  # noqa: BLE001
            logger.debug("underlying bus does not support subscribe_broadcast")

    # _dispatch_to_wrapper_subscribers removed — replaced by
    # _dispatch_standalone (called from publish in standalone mode).

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> BusConfig:
        return self._config

    @property
    def repository(self) -> IMessageRepository:
        return self._repository

    @property
    def underlying_bus(self) -> Any:
        return self._bus

    @property
    def event_bus(self) -> Any | None:
        return self._event_bus

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        receiver: str,
        handler: MessageHandler,
    ) -> str:
        """Subscribe ``handler`` to messages addressed to ``receiver``.

        Returns a subscription token that can be passed to
        :meth:`unsubscribe`.
        """
        await self._ensure_tap()
        token = f"sub-{uuid.uuid4().hex[:8]}"
        self._subscribers.setdefault(receiver, {})[token] = handler
        # Also subscribe on the underlying bus (so messages published
        # via the raw MessageBus reach our handler). For standalone
        # mode, _dispatch_standalone handles delivery directly.
        if not isinstance(self._bus, _StandaloneBus):

            async def _wrapped(wire_msg: Any) -> None:
                try:
                    if isinstance(wire_msg, AgentMessage):
                        msg = wire_msg
                    else:
                        msg = AgentMessage.from_message(wire_msg)
                    await handler(msg)
                except Exception:  # noqa: BLE001
                    logger.exception("subscriber handler raised")

            try:
                underlying_token = await self._bus.subscribe(receiver, _wrapped)
                # Track the underlying token so unsubscribe can remove it.
                self._token_map[token] = underlying_token
            except Exception:  # noqa: BLE001
                pass
        return token

    async def subscribe_broadcast(self, handler: MessageHandler) -> str:
        """Subscribe ``handler`` to all broadcast messages (``receiver == "*"``)."""
        await self._ensure_tap()
        token = f"bc-{uuid.uuid4().hex[:8]}"
        self._broadcast_subscribers[token] = handler
        if not isinstance(self._bus, _StandaloneBus):

            async def _wrapped(wire_msg: Any) -> None:
                try:
                    if isinstance(wire_msg, AgentMessage):
                        msg = wire_msg
                    else:
                        msg = AgentMessage.from_message(wire_msg)
                    await handler(msg)
                except Exception:  # noqa: BLE001
                    logger.exception("broadcast handler raised")

            try:
                underlying_token = await self._bus.subscribe_broadcast(_wrapped)
                self._token_map[token] = underlying_token
            except Exception:  # noqa: BLE001
                pass
        return token

    async def unsubscribe(self, token: str) -> bool:
        """Remove a subscription. Returns True if found."""
        found = False
        for subs in self._subscribers.values():
            if token in subs:
                del subs[token]
                found = True
                break
        if not found and token in self._broadcast_subscribers:
            del self._broadcast_subscribers[token]
            found = True
        if found:
            # Also unsubscribe from the underlying bus.
            underlying_token = self._token_map.pop(token, None)
            if underlying_token is not None:
                try:
                    await self._bus.unsubscribe(underlying_token)
                except Exception:  # noqa: BLE001
                    pass
            return True
        # Try the underlying bus directly.
        try:
            return await self._bus.unsubscribe(token)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, message: AgentMessage) -> int:
        """Publish ``message`` to all matching subscribers.

        Returns the number of subscribers the message was delivered to.
        """
        await self._ensure_tap()
        # Apply default TTL.
        if message.ttl == 0 and self._config.default_ttl > 0:
            message.ttl = self._config.default_ttl
        # TTL enforcement.
        if self._config.enforce_ttl and message.is_expired:
            self._metrics["dropped_expired"] += 1
            logger.debug("dropping expired message %s", message.message_id)
            return 0
        # Record (persist + history + metrics + bridge).
        await self._record_message(message)
        # Deliver.
        delivered = 0
        try:
            if isinstance(self._bus, _StandaloneBus):
                # Standalone mode: dispatch to wrapper subscribers directly.
                delivered = await self._dispatch_standalone(message)
            else:
                # Wire mode: convert and forward to the underlying bus.
                wire_msg = message.to_message()
                delivered = await self._bus.publish(wire_msg)
                # In wire mode, the underlying MessageBus only invokes
                # broadcast subscribers for broadcast messages (receiver="*").
                # Point-to-point messages go only to the receiver's direct
                # subscribers, so the CollaborationManager's broadcast
                # subscription never fires for responses. To fix this, we
                # also notify our wrapper's broadcast subscribers for
                # point-to-point messages (broadcast messages are already
                # handled by the underlying bus). This ensures the
                # CollaborationManager's auto-subscribe handler sees all
                # messages, including point-to-point responses, without
                # double-delivering broadcast messages.
                if not message.is_broadcast:
                    for handler in list(self._broadcast_subscribers.values()):
                        try:
                            await handler(message)
                        except Exception:  # noqa: BLE001
                            logger.exception("wrapper broadcast handler raised")
            self._metrics["delivered_total"] += delivered
        except Exception as exc:
            raise MessageDeliveryError(message.message_id, str(exc)) from exc
        if delivered == 0 and not message.is_broadcast:
            self._metrics["dropped_no_subscriber"] += 1
        return delivered

    async def _record_message(self, message: AgentMessage) -> None:
        """Persist, append to history, update metrics, and bridge to event bus."""
        if self._config.persist_messages:
            try:
                self._repository.save(message)
            except Exception:  # noqa: BLE001
                self._metrics["persist_errors"] += 1
                logger.exception("failed to persist message %s", message.message_id)
        async with self._lock:
            self._history.append(message)
            if len(self._history) > self._config.max_history:
                self._history = self._history[-self._config.max_history :]
            self._metrics["published_total"] += 1
            self._metrics["by_type"][message.message_type.value] = (
                self._metrics["by_type"].get(message.message_type.value, 0) + 1
            )
        if self._config.bridge_to_event_bus and self._event_bus is not None:
            try:
                await self._event_bus.publish(
                    f"agent.message.{message.message_type.value}",
                    source=message.sender_agent,
                    payload=message.serialize(),
                    correlation_id=message.correlation_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("failed to bridge message to event bus")

    async def _dispatch_standalone(self, message: AgentMessage) -> int:
        """Dispatch ``message`` to wrapper subscribers in standalone mode.

        Returns the number of handlers invoked.
        """
        delivered = 0
        # Point-to-point.
        if not message.is_broadcast:
            for handler in list(self._subscribers.get(message.receiver_agent, {}).values()):
                try:
                    await handler(message)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    logger.exception("subscriber handler raised")
        # Broadcast.
        for handler in list(self._broadcast_subscribers.values()):
            try:
                await handler(message)
                delivered += 1
            except Exception:  # noqa: BLE001
                logger.exception("broadcast handler raised")
        return delivered

    async def publish_many(self, messages: list[AgentMessage]) -> dict[str, int]:
        """Publish a batch of messages. Returns ``{message_id: delivered_count}``."""
        results: dict[str, int] = {}
        for m in messages:
            results[m.message_id] = await self.publish(m)
        return results

    # ------------------------------------------------------------------
    # Convenience senders
    # ------------------------------------------------------------------

    async def send_task_request(
        self,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: float = 0.0,
    ) -> AgentMessage:
        """Build and publish a ``TASK_REQUEST``. Returns the message."""
        msg = AgentMessage.task_request(
            sender,
            receiver,
            task_id=task_id,
            payload=payload,
            correlation_id=correlation_id,
            priority=priority,
            ttl=ttl,
        )
        await self.publish(msg)
        return msg

    async def send_task_response(
        self,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        in_reply_to: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build and publish a ``TASK_RESPONSE``."""
        msg = AgentMessage.task_response(
            sender,
            receiver,
            task_id=task_id,
            payload=payload,
            in_reply_to=in_reply_to,
            correlation_id=correlation_id,
        )
        await self.publish(msg)
        return msg

    async def send_status_update(
        self,
        sender: str,
        receiver: str = "*",
        *,
        status: str = "",
        progress: float | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build and publish a ``STATUS_UPDATE`` (default broadcast)."""
        msg = AgentMessage.status_update(
            sender,
            receiver,
            status=status,
            progress=progress,
            correlation_id=correlation_id,
        )
        await self.publish(msg)
        return msg

    async def send_error_report(
        self,
        sender: str,
        receiver: str = "orchestrator",
        *,
        error: str = "",
        code: str = "INTERNAL",
        task_id: str | None = None,
        in_reply_to: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build and publish an ``ERROR_REPORT``."""
        msg = AgentMessage.error_report(
            sender,
            receiver,
            error=error,
            code=code,
            task_id=task_id,
            in_reply_to=in_reply_to,
            correlation_id=correlation_id,
        )
        await self.publish(msg)
        return msg

    async def send_handoff(
        self,
        sender: str,
        receiver: str,
        *,
        task_id: str | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build and publish a ``HANDOFF``."""
        msg = AgentMessage.handoff(
            sender,
            receiver,
            task_id=task_id,
            reason=reason,
            payload=payload,
            correlation_id=correlation_id,
        )
        await self.publish(msg)
        return msg

    async def send_context_transfer(
        self,
        sender: str,
        receiver: str,
        *,
        context: dict[str, Any],
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        """Build and publish a ``CONTEXT_TRANSFER``."""
        msg = AgentMessage.context_transfer(
            sender,
            receiver,
            context=context,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        await self.publish(msg)
        return msg

    # ------------------------------------------------------------------
    # History / queries
    # ------------------------------------------------------------------

    def history(self, *, limit: int = 100) -> list[AgentMessage]:
        """Return recent messages from the in-bus ring buffer (newest last)."""
        return list(self._history[-limit:])

    def history_by_correlation(self, correlation_id: str) -> list[AgentMessage]:
        """Return all messages sharing ``correlation_id`` (oldest first)."""
        return [m for m in self._history if m.correlation_id == correlation_id]

    def history_by_sender(self, sender: str, *, limit: int = 100) -> list[AgentMessage]:
        """Return messages sent by ``sender`` (newest first)."""
        return [m for m in reversed(self._history) if m.sender_agent == sender][:limit]

    def history_by_receiver(self, receiver: str, *, limit: int = 100) -> list[AgentMessage]:
        """Return messages addressed to ``receiver`` (newest first)."""
        return [
            m for m in reversed(self._history) if m.receiver_agent == receiver or m.is_broadcast
        ][:limit]

    def get_message(self, message_id: str) -> AgentMessage | None:
        """Return the message with ``message_id`` from the repository."""
        return self._repository.get(message_id)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """Return a snapshot of bus metrics."""
        return {
            **self._metrics,
            "subscriber_count": sum(len(s) for s in self._subscribers.values()),
            "broadcast_subscriber_count": len(self._broadcast_subscribers),
            "history_size": len(self._history),
            "repository_count": self._repository.count(),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        """Clear in-bus history (does NOT clear the repository)."""
        async with self._lock:
            self._history.clear()

    async def clear_all(self) -> None:
        """Clear in-bus history AND the repository."""
        async with self._lock:
            self._history.clear()
            self._repository.clear()
            # Reset metrics.
            self._metrics = {
                "published_total": 0,
                "delivered_total": 0,
                "dropped_expired": 0,
                "dropped_no_subscriber": 0,
                "persist_errors": 0,
                "by_type": {t.value: 0 for t in MessageType},
            }

    # ------------------------------------------------------------------
    # Internals (legacy _setup_underlying_bus_listener removed; tap is
    # installed lazily by _ensure_tap / _install_tap above).
    # ------------------------------------------------------------------


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _create_default_bus() -> Any:
    """Create a default underlying MessageBus (lazy import)."""
    try:
        from multiagent.messages import MessageBus

        return MessageBus()
    except ImportError:
        # Fall back to a minimal in-memory bus for standalone usage.
        return _StandaloneBus()


class _StandaloneBus:
    """Minimal in-memory bus used when multiagent is not available.

    Implements just enough of the MessageBus API
    (``subscribe`` / ``subscribe_broadcast`` / ``publish`` / ``unsubscribe``)
    for the :class:`AgentMessageBus` to work standalone.
    """

    def __init__(self) -> None:
        self._subs: dict[str, dict[str, Any]] = {}
        self._broadcast: dict[str, Any] = {}
        self._pending: dict[str, list[Any]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, receiver: str, handler: Any) -> str:
        token = f"sub-{uuid.uuid4().hex[:8]}"
        self._subs.setdefault(receiver, {})[token] = handler
        # Drain pending.
        for msg in self._pending.pop(receiver, []):
            try:
                await handler(msg)
            except Exception:  # noqa: BLE001
                pass
        return token

    async def subscribe_broadcast(self, handler: Any) -> str:
        token = f"bc-{uuid.uuid4().hex[:8]}"
        self._broadcast[token] = handler
        return token

    async def unsubscribe(self, token: str) -> bool:
        for subs in self._subs.values():
            if token in subs:
                del subs[token]
                return True
        if token in self._broadcast:
            del self._broadcast[token]
            return True
        return False

    async def publish(self, message: Any) -> int:
        delivered = 0
        receiver = getattr(message, "receiver", "")
        # Point-to-point.
        if receiver and receiver != "*":
            for handler in list(self._subs.get(receiver, {}).values()):
                try:
                    await handler(message)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    pass
            if delivered == 0:
                self._pending.setdefault(receiver, []).append(message)
        # Broadcast.
        for handler in list(self._broadcast.values()):
            try:
                await handler(message)
                delivered += 1
            except Exception:  # noqa: BLE001
                pass
        return delivered

    def stats(self) -> dict[str, int]:
        return {
            "subscribers": sum(len(s) for s in self._subs.values()),
            "broadcast_subscribers": len(self._broadcast),
            "pending": sum(len(p) for p in self._pending.values()),
        }


def assert_agent_known(agent_name: str, known_agents: set[str]) -> None:
    """Raise :class:`AgentNotFoundError` if ``agent_name`` is not in ``known_agents``.

    Convenience helper used by the CollaborationManager.
    """
    if agent_name not in known_agents and agent_name != "*" and agent_name != "orchestrator":
        raise AgentNotFoundError(agent_name)


__all__ = [
    "AgentMessageBus",
    "BusConfig",
    "MessageHandler",
    "assert_agent_known",
]
