"""Shared protocol definitions for the core package.

These protocols are intentionally isolated in their own module so they
can be imported by both `orchestrator.py` and `agent_router.py`
without creating a circular dependency.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Agent(Protocol):
    """Protocol every agent must satisfy."""

    name: str

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        """Handle a user message and return a response."""


@runtime_checkable
class Memory(Protocol):
    """Protocol every memory backend must satisfy."""

    async def store(self, key: str, value: Any) -> None: ...

    async def retrieve(self, key: str) -> Any | None: ...


@runtime_checkable
class Tool(Protocol):
    """Protocol every tool must satisfy."""

    name: str

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]: ...
