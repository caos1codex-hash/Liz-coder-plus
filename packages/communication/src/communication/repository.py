"""Message persistence — repository pattern over the existing memory layer (Sprint 2.9).

The :class:`MessageRepository` persists inter-agent messages using the
existing Sprint 1.8 ``ExecutionRepository`` as a backend. This avoids
introducing a new SQLite schema migration: each message is recorded as
an ``execution_history`` row with ``event_type='agent.message'`` and
the full :class:`AgentMessage.serialize()` payload stored as JSON in
the ``metadata`` column.

Two backends are provided:

- :class:`InMemoryMessageRepository` — for tests and standalone usage.
  Stores messages in a list, supports queries by correlation_id,
  sender, receiver, task_id, message_type, and time range.
- :class:`MessageRepository` — wraps an existing
  :class:`memory.repositories.ExecutionRepository` (or any duck-typed
  object exposing ``record(...)`` / ``get_by_event_type(...)`` /
  ``get_recent(...)``). Lazy-imports the memory package so this package
  has no hard dependency on it.

Both backends implement the same interface (:class:`IMessageRepository`),
so the :class:`AgentMessageBus` and :class:`CollaborationManager` can
use either transparently.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from .exceptions import PersistenceError
from .protocol import AgentMessage, MessageType

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Protocol
# ----------------------------------------------------------------------


@runtime_checkable
class IMessageRepository(Protocol):
    """Interface implemented by all message repository backends."""

    def save(self, message: AgentMessage) -> str:
        """Persist ``message``. Returns the stored message_id."""
        ...

    def get(self, message_id: str) -> AgentMessage | None:
        """Return the message with ``message_id``, or None."""
        ...

    def list_by_correlation(self, correlation_id: str) -> list[AgentMessage]:
        """Return all messages sharing ``correlation_id``, oldest first."""
        ...

    def list_by_sender(self, sender: str, *, limit: int = 100) -> list[AgentMessage]:
        """Return messages sent by ``sender``, newest first."""
        ...

    def list_by_receiver(self, receiver: str, *, limit: int = 100) -> list[AgentMessage]:
        """Return messages addressed to ``receiver``, newest first."""
        ...

    def list_by_task(self, task_id: str) -> list[AgentMessage]:
        """Return all messages related to ``task_id``."""
        ...

    def list_by_type(self, message_type: MessageType, *, limit: int = 100) -> list[AgentMessage]:
        """Return messages of ``message_type``, newest first."""
        ...

    def list_recent(self, *, limit: int = 100) -> list[AgentMessage]:
        """Return recent messages, newest first."""
        ...

    def count(self) -> int:
        """Total number of stored messages."""
        ...

    def clear(self) -> int:
        """Delete all stored messages. Returns the deleted count."""
        ...


# ----------------------------------------------------------------------
# In-memory backend
# ----------------------------------------------------------------------


class InMemoryMessageRepository:
    """In-memory implementation of :class:`IMessageRepository`.

    Stores messages in a list. Suitable for tests and ephemeral
    sessions. All queries are O(N).
    """

    def __init__(self, *, max_size: int = 10_000) -> None:
        self._messages: list[AgentMessage] = []
        self._by_id: dict[str, AgentMessage] = {}
        self._max_size = max_size

    def save(self, message: AgentMessage) -> str:
        self._messages.append(message)
        self._by_id[message.message_id] = message
        # Trim if over capacity (drop oldest).
        if len(self._messages) > self._max_size:
            dropped = self._messages.pop(0)
            self._by_id.pop(dropped.message_id, None)
        return message.message_id

    def get(self, message_id: str) -> AgentMessage | None:
        return self._by_id.get(message_id)

    def list_by_correlation(self, correlation_id: str) -> list[AgentMessage]:
        return [m for m in self._messages if m.correlation_id == correlation_id]

    def list_by_sender(self, sender: str, *, limit: int = 100) -> list[AgentMessage]:
        return [m for m in reversed(self._messages) if m.sender_agent == sender][:limit]

    def list_by_receiver(self, receiver: str, *, limit: int = 100) -> list[AgentMessage]:
        # Match either direct receiver or broadcast ("*").
        return [
            m for m in reversed(self._messages) if m.receiver_agent == receiver or m.is_broadcast
        ][:limit]

    def list_by_task(self, task_id: str) -> list[AgentMessage]:
        return [m for m in self._messages if m.task_id == task_id]

    def list_by_type(self, message_type: MessageType, *, limit: int = 100) -> list[AgentMessage]:
        return [m for m in reversed(self._messages) if m.message_type == message_type][:limit]

    def list_recent(self, *, limit: int = 100) -> list[AgentMessage]:
        return list(reversed(self._messages))[:limit]

    def count(self) -> int:
        return len(self._messages)

    def clear(self) -> int:
        n = len(self._messages)
        self._messages.clear()
        self._by_id.clear()
        return n


# ----------------------------------------------------------------------
# SQLite-backed repository (wraps ExecutionRepository)
# ----------------------------------------------------------------------


class MessageRepository:
    """SQLite-backed :class:`IMessageRepository`.

    Wraps an existing ``memory.repositories.ExecutionRepository`` (or
    any duck-typed object exposing ``record(...)`` /
    ``get_by_event_type(...)`` / ``get_recent(...)``). Each message is
    stored as an ``execution_history`` row with:

    - ``event_type`` = ``"agent.message"``
    - ``task_id``    = ``message.task_id`` (or ``message.message_id``
                        if ``task_id`` is None, so the row is always
                        retrievable)
    - ``agent_name`` = ``message.sender_agent``
    - ``metadata``   = ``message.serialize()`` (full JSON)
    - ``status``     = ``message.message_type.value``
    """

    EVENT_TYPE = "agent.message"

    def __init__(self, execution_repository: Any) -> None:
        self._repo = execution_repository

    def save(self, message: AgentMessage) -> str:
        try:
            self._repo.record(
                event_type=self.EVENT_TYPE,
                task_id=message.task_id or message.message_id,
                agent_name=message.sender_agent,
                status=message.message_type.value,
                metadata=message.serialize(),
            )
            return message.message_id
        except Exception as exc:
            raise PersistenceError(
                f"failed to save message {message.message_id}: {exc}",
                details={"message_id": message.message_id},
            ) from exc

    def get(self, message_id: str) -> AgentMessage | None:
        # ExecutionRepository doesn't expose get-by-id, so we scan recent.
        for row in self._repo.get_recent(limit=10_000):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("message_id") == message_id:
                return AgentMessage.deserialize(meta)
        return None

    def list_by_correlation(self, correlation_id: str) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_by_event_type(self.EVENT_TYPE, limit=10_000):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("correlation_id") == correlation_id:
                results.append(AgentMessage.deserialize(meta))
        return results

    def list_by_sender(self, sender: str, *, limit: int = 100) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_by_event_type(self.EVENT_TYPE, limit=10_000):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("sender_agent") == sender:
                results.append(AgentMessage.deserialize(meta))
        return list(reversed(results))[:limit]

    def list_by_receiver(self, receiver: str, *, limit: int = 100) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_by_event_type(self.EVENT_TYPE, limit=10_000):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and (
                meta.get("receiver_agent") == receiver or meta.get("receiver_agent") == "*"
            ):
                results.append(AgentMessage.deserialize(meta))
        return list(reversed(results))[:limit]

    def list_by_task(self, task_id: str) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_by_task(task_id, limit=10_000):
            meta = row.get("metadata") or {}
            # Only rows that look like agent messages.
            if isinstance(meta, dict) and "message_id" in meta:
                results.append(AgentMessage.deserialize(meta))
        return results

    def list_by_type(self, message_type: MessageType, *, limit: int = 100) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_by_event_type(self.EVENT_TYPE, limit=10_000):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("message_type") == message_type.value:
                results.append(AgentMessage.deserialize(meta))
        return list(reversed(results))[:limit]

    def list_recent(self, *, limit: int = 100) -> list[AgentMessage]:
        results: list[AgentMessage] = []
        for row in self._repo.get_recent(limit=limit * 2):
            meta = row.get("metadata") or {}
            if isinstance(meta, dict) and "message_id" in meta:
                results.append(AgentMessage.deserialize(meta))
        return list(reversed(results))[:limit]

    def count(self) -> int:
        counts = self._repo.count_by_event_type()
        return int(counts.get(self.EVENT_TYPE, 0))

    def clear(self) -> int:
        # ExecutionRepository doesn't expose bulk-delete by event_type.
        # We can only clear by task_id or workflow_id. As a fallback,
        # we return 0 (callers should use InMemoryMessageRepository
        # for tests that need clear()).
        logger.warning("MessageRepository.clear() is a no-op on the SQLite backend")
        return 0


__all__ = [
    "IMessageRepository",
    "InMemoryMessageRepository",
    "MessageRepository",
]
