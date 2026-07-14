"""Liz Coder Plus — Intelligent Task Planner & Execution Engine (Sprint 2.8).

This package provides the planner for Liz Coder Plus. It decomposes
high-level user requests into optimized execution plans represented as
DAGs of :class:`Task` objects, assigns each task to the best available
agent, and provides a full :class:`Scheduler` that executes the plan
respecting dependencies, priorities, retries, and recovery policies.

Public API::

    from planner import (
        # Core models
        Task, TaskStatus, TaskPriority, Difficulty,
        # Plan & graph
        ExecutionPlan, DAG,
        # Scheduler
        Scheduler, SchedulerConfig,
        # Planner & components
        TaskPlanner, PlannerConfig,
        TaskDecomposer, DecomposerConfig,
        DependencyAnalyzer, DependencyAnalyzerConfig,
        CapabilityMatcher, MatcherWeights,
        AgentRegistryAdapter, AgentInfo,
        CostEstimator, EstimatorConfig, Estimate,
        # Exceptions
        PlannerError, TaskNotFoundError, CycleDetectedError,
        PlanValidationError, AgentAssignmentError,
        # Utilities
        new_task_id, now_iso,
    )

Integration with existing modules (Sprint 2.1-2.7) is provided through
thin adapters, so this package has zero hard dependencies on the
multiagent / memory / workflow packages at import time.
"""

from __future__ import annotations

# Version
__version__ = "0.1.0"

# Exceptions
from .capability_matcher import (
    AgentInfo,
    AgentRegistryAdapter,
    CapabilityMatcher,
    MatcherWeights,
)
from .dependency_analyzer import (
    DependencyAnalyzer,
    DependencyAnalyzerConfig,
)
from .estimator import (
    CostEstimator,
    Estimate,
    EstimatorConfig,
)
from .exceptions import (
    AgentAssignmentError,
    CycleDetectedError,
    DependencyError,
    EstimationError,
    InvalidTaskError,
    PlannerError,
    PlanValidationError,
    SchedulerError,
    SchedulerPausedError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from .execution_plan import ExecutionPlan

# Graph & plan
from .graph import DAG, assert_dependency_satisfied, validate_no_cycles

# Integration adapters (Sprint 2.1-2.7 modules)
from .integration import (
    MultiAgentRegistryAdapter,
    SemanticMemoryAdapter,
    WorkflowEngineAdapter,
    build_integrated_planner,
)

# Interfaces (Protocols)
from .interfaces import (
    IDAG,
    IAgentRegistryAdapter,
    ICapabilityMatcher,
    ICostEstimator,
    IDependencyAnalyzer,
    IExecutionPlan,
    IScheduler,
    ISemanticMemoryAdapter,
    ITask,
    ITaskDecomposer,
)

# Models & enums
from .models import (
    AgentAssignment,
    CapabilityScore,
    Difficulty,
    PlanStatistics,
    TaskPriority,
    TaskStatus,
    can_transition,
    difficulty_multiplier,
    priority_weight,
)

# Top-level planner
from .planner import PlannerConfig, TaskPlanner

# Scheduler
from .scheduler import Scheduler, SchedulerConfig

# Task & utils
from .task import Task

# Decomposer / analyzer / estimator / matcher
from .task_decomposer import (
    DecomposerConfig,
    DecompositionTemplate,
    TaskDecomposer,
)
from .utils import (
    clamp,
    coerce_float,
    coerce_int,
    merge_dicts,
    new_task_id,
    now_epoch,
    now_iso,
    parse_bool,
    safe_get,
)

__all__ = [
    # Exceptions
    "AgentAssignmentError",
    "CycleDetectedError",
    "DependencyError",
    "EstimationError",
    "InvalidTaskError",
    "PlanValidationError",
    "PlannerError",
    "SchedulerError",
    "SchedulerPausedError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
    # Models
    "AgentAssignment",
    "CapabilityScore",
    "Difficulty",
    "PlanStatistics",
    "TaskPriority",
    "TaskStatus",
    "can_transition",
    "difficulty_multiplier",
    "priority_weight",
    # Task & utils
    "Task",
    "clamp",
    "coerce_float",
    "coerce_int",
    "merge_dicts",
    "new_task_id",
    "now_epoch",
    "now_iso",
    "parse_bool",
    "safe_get",
    # Graph & plan
    "DAG",
    "ExecutionPlan",
    "assert_dependency_satisfied",
    "validate_no_cycles",
    # Scheduler
    "Scheduler",
    "SchedulerConfig",
    # Components
    "AgentInfo",
    "AgentRegistryAdapter",
    "CapabilityMatcher",
    "CostEstimator",
    "DecomposerConfig",
    "DecompositionTemplate",
    "DependencyAnalyzer",
    "DependencyAnalyzerConfig",
    "Estimate",
    "EstimatorConfig",
    "MatcherWeights",
    "TaskDecomposer",
    # Top-level
    "PlannerConfig",
    "TaskPlanner",
    # Integration
    "MultiAgentRegistryAdapter",
    "SemanticMemoryAdapter",
    "WorkflowEngineAdapter",
    "build_integrated_planner",
    # Interfaces
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
    # Version
    "__version__",
]
