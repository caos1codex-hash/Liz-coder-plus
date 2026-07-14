"""Abstract interfaces (Protocols) for the planner package (Sprint 2.8).

The planner is designed around a small set of Protocols so that each
component can be replaced or mocked in tests without dragging in
concrete dependencies.

The Protocols are intentionally minimal: they describe only the
operations the rest of the planner depends on. Concrete implementations
(``TaskPlanner``, ``Scheduler``, ``CapabilityMatcher``, ``CostEstimator``)
live in their own modules and are not required to subclass these
Protocols — structural typing is sufficient.

Usage::

    from typing import Protocol
    from planner.interfaces import ICapabilityMatcher

    def use_matcher(m: ICapabilityMatcher) -> None:
        ...
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ----------------------------------------------------------------------
# Task / Plan
# ----------------------------------------------------------------------


@runtime_checkable
class ITask(Protocol):
    """Minimal task interface used by graph / scheduler."""

    id: str
    status: Any  # TaskStatus
    dependencies: list[str]
    required_capabilities: list[str]

    def serialize(self) -> dict[str, Any]: ...
    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> ITask: ...


@runtime_checkable
class IExecutionPlan(Protocol):
    """Minimal plan interface used by the planner."""

    @property
    def id(self) -> str: ...

    def add_task(self, task: Any) -> None: ...
    def remove_task(self, task_id: str) -> bool: ...
    def validate(self) -> list[str]: ...
    def serialize(self) -> dict[str, Any]: ...
    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> IExecutionPlan: ...


# ----------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------


@runtime_checkable
class IDAG(Protocol):
    """Minimal DAG interface used by the scheduler."""

    def add_task(self, task_id: str) -> None: ...
    def remove_task(self, task_id: str) -> None: ...
    def add_dependency(self, task_id: str, depends_on: str) -> None: ...
    def remove_dependency(self, task_id: str, depends_on: str) -> None: ...
    def parents(self, task_id: str) -> list[str]: ...
    def children(self, task_id: str) -> list[str]: ...
    def roots(self) -> list[str]: ...
    def leaves(self) -> list[str]: ...
    def topological_sort(self) -> list[str]: ...
    def detect_cycles(self) -> list[list[str]]: ...
    def validate(self) -> list[str]: ...


# ----------------------------------------------------------------------
# Scheduler
# ----------------------------------------------------------------------


@runtime_checkable
class IScheduler(Protocol):
    """Minimal scheduler interface used by the orchestrator."""

    def next_tasks(self, *, max_tasks: int = 1) -> list[Any]: ...
    def mark_started(self, task_id: str) -> None: ...
    def mark_completed(self, task_id: str, *, result: Any = None) -> None: ...
    def mark_failed(self, task_id: str, *, error: str = "") -> None: ...
    def retry(self, task_id: str) -> int: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def cancel(self, task_id: str) -> None: ...


# ----------------------------------------------------------------------
# Decomposer / Dependency Analyzer
# ----------------------------------------------------------------------


@runtime_checkable
class ITaskDecomposer(Protocol):
    """Decomposes a high-level objective into subtasks."""

    def decompose(self, objective: str, *, context: dict[str, Any] | None = None) -> list[Any]: ...


@runtime_checkable
class IDependencyAnalyzer(Protocol):
    """Infers dependencies between tasks."""

    def analyze(self, tasks: list[Any]) -> dict[str, list[str]]: ...


# ----------------------------------------------------------------------
# Capability matcher / Estimator
# ----------------------------------------------------------------------


@runtime_checkable
class ICapabilityMatcher(Protocol):
    """Matches tasks to agents by capability."""

    def match(self, task: Any) -> Any: ...


@runtime_checkable
class ICostEstimator(Protocol):
    """Estimates time / tokens / cost / difficulty for a task."""

    def estimate(self, task: Any) -> dict[str, Any]: ...


# ----------------------------------------------------------------------
# Registry / Memory adapters
# ----------------------------------------------------------------------


@runtime_checkable
class IAgentRegistryAdapter(Protocol):
    """Adapter over an external ``AgentRegistry``.

    The planner never imports ``AgentRegistry`` directly; instead it
    consumes this minimal interface so that the planner package can be
    used standalone (with a stub adapter) or wired into the existing
    multi-agent registry (with a real adapter).
    """

    def list_agents(self) -> list[dict[str, Any]]: ...
    def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...
    def find_by_capability(self, capability: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class ISemanticMemoryAdapter(Protocol):
    """Adapter over an external semantic memory / retrieval engine."""

    def retrieve_similar(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]: ...


__all__ = [
    "IAgentRegistryAdapter",
    "ICapabilityMatcher",
    "ICostEstimator",
    "IDAG",
    "IDependencyAnalyzer",
    "IExecutionPlan",
    "IScheduler",
    "ISemanticMemoryAdapter",
    "ITask",
    "ITaskDecomposer",
]
