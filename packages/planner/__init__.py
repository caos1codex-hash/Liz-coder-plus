"""Planner package — Intelligent Task Planning Engine.

Sprint 3.1 — Liz Coder Plus v0.1.1.

This package provides the structural foundation for task planning.
It creates, validates, serializes, and analyses execution plans
without performing any actual task execution.

Public API
----------
.. autoclass:: TaskPlanner
.. autoclass:: PlanValidator
.. autoclass:: PlanSerializer
.. autoclass:: PlannerMetrics

Models
------
.. autoclass:: TaskPlan
.. autoclass:: PlanStep
.. autoclass:: PlanMetrics

Enums
-----
.. autoenum:: PlanStatus
.. autoenum:: StepStatus
.. autoenum:: Priority
"""

# -- enums ------------------------------------------------------------------
from .enums import (
    PlanStatus,
    Priority,
    StepStatus,
    can_transition_plan,
    can_transition_step,
)

# -- exceptions -------------------------------------------------------------
from .exceptions import (
    CircularDependencyError,
    DependencyError,
    DuplicateStepError,
    InvalidStatusError,
    PlannerException,
    PlanValidationError,
    SerializationError,
)

# -- interfaces -------------------------------------------------------------
from .interfaces import IMetrics, IPlanner, ISerializer, IValidator

# -- implementations --------------------------------------------------------
from .metrics import PlannerMetrics

# -- models -----------------------------------------------------------------
from .models import PlanMetrics, PlanStep, TaskPlan
from .planner import TaskPlanner
from .serializer import PlanSerializer
from .validator import PlanValidator

__all__ = [
    # models
    "TaskPlan",
    "PlanStep",
    "PlanMetrics",
    # enums
    "PlanStatus",
    "StepStatus",
    "Priority",
    "can_transition_plan",
    "can_transition_step",
    # exceptions
    "PlannerException",
    "PlanValidationError",
    "SerializationError",
    "DependencyError",
    "CircularDependencyError",
    "DuplicateStepError",
    "InvalidStatusError",
    # interfaces
    "IPlanner",
    "IValidator",
    "ISerializer",
    "IMetrics",
    # implementations
    "TaskPlanner",
    "PlanValidator",
    "PlanSerializer",
    "PlannerMetrics",
]
