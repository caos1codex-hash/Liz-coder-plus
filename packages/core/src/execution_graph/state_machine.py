"""Execution State Machine.

Sprint 3.8.

Provides a configurable state machine for tracking node execution
states. Validates transitions and maintains a history log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import ExecutionNodeState, can_transition_node

logger = logging.getLogger(__name__)


@dataclass
class StateTransition:
    """A single recorded state transition.

    Attributes:
        node_name:  Name of the node that transitioned.
        from_state: Previous state.
        to_state:   New state.
        timestamp:  ISO timestamp of the transition.
        metadata:   Optional metadata about the transition.
    """

    node_name: str
    from_state: ExecutionNodeState
    to_state: ExecutionNodeState
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_name": self.node_name,
            "from": self.from_state.value,
            "to": self.to_state.value,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class ExecutionStateMachine:
    """State machine for tracking execution node states.

    Wraps the transition logic from ExecutionNodeState with
    validation, logging, and history tracking.

    Args:
        max_history: Maximum number of transitions to keep.
    """

    def __init__(self, *, max_history: int = 1000) -> None:
        self._states: dict[str, ExecutionNodeState] = {}
        self._history: list[StateTransition] = []
        self._max_history = max_history

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    def get_state(self, node_name: str) -> ExecutionNodeState | None:
        """Return the current state of a node, or None if unknown."""
        return self._states.get(node_name)

    def set_state(
        self,
        node_name: str,
        state: ExecutionNodeState,
        *,
        force: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Set the state of a node (unconditionally if force=True).

        Args:
            node_name: Name of the node.
            state:     New state.
            force:     If True, skip transition validation.
            metadata:  Optional metadata for the transition.

        Raises:
            ValueError: If the transition is not valid and force=False.
        """
        old_state = self._states.get(node_name, ExecutionNodeState.PENDING)

        if not force and not can_transition_node(old_state, state):
            raise ValueError(
                f"Node '{node_name}' cannot transition from "
                f"{old_state.value} to {state.value}"
            )

        transition = StateTransition(
            node_name=node_name,
            from_state=old_state,
            to_state=state,
            metadata=metadata or {},
        )

        self._states[node_name] = state
        self._record(transition)

        logger.debug(
            "StateMachine: '%s' %s -> %s",
            node_name, old_state.value, state.value,
        )

    def transition(
        self,
        node_name: str,
        new_state: ExecutionNodeState,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> StateTransition:
        """Transition a node to a new state with validation.

        Args:
            node_name: Name of the node.
            new_state: Target state.
            metadata:  Optional metadata.

        Returns:
            The StateTransition that was recorded.

        Raises:
            ValueError: If the transition is not valid.
        """
        self.set_state(node_name, new_state, metadata=metadata)
        return self._history[-1]

    def validate_transition(
        self,
        node_name: str,
        target_state: ExecutionNodeState,
    ) -> bool:
        """Check if a transition would be valid without performing it."""
        current = self._states.get(node_name, ExecutionNodeState.PENDING)
        return can_transition_node(current, target_state)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def history(
        self,
        *,
        node_name: str | None = None,
        limit: int = 100,
    ) -> list[StateTransition]:
        """Return transition history.

        Args:
            node_name: Filter by node name (None = all nodes).
            limit:     Maximum entries to return.
        """
        entries = self._history
        if node_name is not None:
            entries = [e for e in entries if e.node_name == node_name]
        return list(entries[-limit:])

    def _record(self, transition: StateTransition) -> None:
        """Record a transition in the history."""
        self._history.append(transition)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def initialize(self, node_names: list[str]) -> None:
        """Initialize all nodes to PENDING state."""
        for name in node_names:
            if name not in self._states:
                self._states[name] = ExecutionNodeState.PENDING

    def all_states(self) -> dict[str, ExecutionNodeState]:
        """Return a copy of all current states."""
        return dict(self._states)

    def reset(self) -> None:
        """Reset the state machine."""
        self._states.clear()
        self._history.clear()

    @property
    def node_count(self) -> int:
        return len(self._states)

    @property
    def history_size(self) -> int:
        return len(self._history)

    def to_dict(self) -> dict[str, Any]:
        return {
            "states": {n: s.value for n, s in self._states.items()},
            "history_size": self.history_size,
            "node_count": self.node_count,
        }


__all__ = ["ExecutionStateMachine", "StateTransition"]