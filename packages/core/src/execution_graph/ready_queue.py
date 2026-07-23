"""Dynamic Ready Queue for execution graph.

Sprint 3.8.

Maintains a priority-ordered queue of nodes whose dependencies are
all satisfied. Nodes are pushed when they become ready (all deps
completed) and popped for execution.
"""

from __future__ import annotations

import heapq
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ReadyQueue:
    """Dynamic priority queue for nodes ready to execute.

    A node enters the queue when all its dependencies have been
    marked as completed. Nodes are ordered by (priority, name)
    to ensure deterministic scheduling.

    Args:
        max_size: Maximum queue size (0 = unlimited).
    """

    def __init__(self, *, max_size: int = 0) -> None:
        self._heap: list[tuple[int, int, str]] = []  # (priority, counter, name)
        self._counter: int = 0
        self._entries: dict[str, int] = {}  # name -> count in heap
        self._max_size = max_size

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def push(self, name: str, *, priority: int = 0) -> bool:
        """Add a node to the ready queue.

        Args:
            name:     Node name.
            priority: Scheduling priority (lower = higher priority).

        Returns:
            True if the node was added, False if the queue is full
            or the node is already in the queue.
        """
        if self._max_size > 0 and len(self._heap) >= self._max_size:
            logger.warning(
                "ReadyQueue full (%d/%d), rejecting '%s'",
                len(self._heap), self._max_size, name,
            )
            return False

        if name in self._entries:
            return False  # Already queued.

        entry = (priority, self._counter, name)
        self._counter += 1
        heapq.heappush(self._heap, entry)
        self._entries[name] = self._entries.get(name, 0) + 1
        logger.debug("ReadyQueue: pushed '%s' (priority=%d)", name, priority)
        return True

    def pop(self) -> str | None:
        """Remove and return the highest-priority ready node.

        Returns:
            Node name, or None if the queue is empty.
        """
        while self._heap:
            priority, _counter, name = heapq.heappop(self._heap)
            if name in self._entries:
                self._entries[name] -= 1
                if self._entries[name] <= 0:
                    del self._entries[name]
                logger.debug("ReadyQueue: popped '%s'", name)
                return name
        return None

    def next_ready(self) -> str | None:
        """Peek at the next ready node without removing it.

        Returns:
            Node name, or None if the queue is empty.
        """
        for priority, _counter, name in self._heap:
            if name in self._entries:
                return name
        return None

    def mark_completed(self, name: str) -> None:
        """Remove a node from the queue (e.g., after completion).

        This is a no-op if the node is not in the queue. It handles
        the case where a node was completed directly without being
        popped from the queue.
        """
        if name in self._entries:
            del self._entries[name]
            logger.debug("ReadyQueue: marked '%s' as completed", name)

    def mark_failed(self, name: str) -> None:
        """Remove a node from the queue after failure.

        The node is simply removed from the ready set; the caller
        is responsible for handling retry logic.
        """
        if name in self._entries:
            del self._entries[name]
            logger.debug("ReadyQueue: marked '%s' as failed", name)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def empty(self) -> bool:
        """Return True if no nodes are ready."""
        return not self._has_entries()

    @property
    def size(self) -> int:
        """Return the number of nodes currently in the queue."""
        return sum(self._entries.values())

    def contains(self, name: str) -> bool:
        """Return True if the node is in the ready queue."""
        return name in self._entries

    def clear(self) -> None:
        """Remove all entries from the queue."""
        self._heap.clear()
        self._entries.clear()
        self._counter = 0

    def to_list(self) -> list[str]:
        """Return all ready node names in priority order."""
        seen: set[str] = set()
        result: list[str] = []
        for _priority, _counter, name in sorted(self._heap):
            if name in self._entries and name not in seen:
                result.append(name)
                seen.add(name)
        return result

    def _has_entries(self) -> bool:
        """Check if there are any valid entries."""
        return bool(self._entries)

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return f"ReadyQueue(size={self.size}, items={self.to_list()})"


__all__ = ["ReadyQueue"]