"""Execution Graph Package.

Sprint 3.8.

Provides DAG-based parallel execution for plans, including:
- DependencyGraph construction and validation
- Topological sort (Kahn's algorithm)
- Dynamic ready queue
- Parallel scheduling and execution
- Execution state machine
- Configurable retry policies
- Failure recovery and partial replanning
"""

from .dependency_graph import DependencyCycleError, DependencyGraph
from .models import ExecutionNode, ExecutionNodeState, ExecutionResult, can_transition_node
from .parallel_executor import NodeExecutor, ParallelExecutor, ParallelExecutorConfig
from .ready_queue import ReadyQueue
from .recovery import RecoveryCheckpoint, RecoveryManager
from .retry_policy import RetryPolicy
from .scheduler import ParallelScheduler, ScheduleDecision, SchedulerConfig
from .state_machine import ExecutionStateMachine, StateTransition
from .topological_sort import kahn_sort, topological_levels

__all__ = [
    # Models
    "ExecutionNode",
    "ExecutionNodeState",
    "ExecutionResult",
    "can_transition_node",
    # Graph
    "DependencyCycleError",
    "DependencyGraph",
    # Sort
    "kahn_sort",
    "topological_levels",
    # Queue
    "ReadyQueue",
    # State machine
    "ExecutionStateMachine",
    "StateTransition",
    # Retry
    "RetryPolicy",
    # Scheduler
    "ParallelScheduler",
    "ScheduleDecision",
    "SchedulerConfig",
    # Executor
    "NodeExecutor",
    "ParallelExecutor",
    "ParallelExecutorConfig",
    # Recovery
    "RecoveryCheckpoint",
    "RecoveryManager",
]