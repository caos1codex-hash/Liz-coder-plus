"""Multi-agent orchestrator package for Liz Coder Plus (Sprint 2.1).

Public API:

- `BaseAgent` — clase abstracta para agentes.
- `AgentOrchestrator` — orquestador central.
- `Message`, `MessageBus` — mensajería.
- `PriorityQueue`, `Task` — cola de tareas.
- `AgentMemory` — memoria por agente.
- `AgentLogger`, `LogEntry` — logs.
- `MultiAgentConfig`, `RetryPolicy` — configuración.
- `HealthReport` — resultado de health().
- Agentes concretos: `PlannerAgent`, `CoderAgent`, `ReviewerAgent`,
  `ResearchAgent`, `TerminalAgent`, `GitAgent`, `MemoryAgent`.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .base import BaseAgent, HealthReport
from .config import MultiAgentConfig, RetryPolicy
from .enums import (
    AgentStatus,
    HealthState,
    MessageType,
    Permission,
    Priority,
    TaskStatus,
)
from .logs import AgentLogger, LogEntry
from .memory import AgentMemory, MemoryEntry
from .messages import Message, MessageBus
from .orchestrator import AgentOrchestrator
from .queue import PriorityQueue, Task

# Agentes concretos
from .agents.coder import CoderAgent
from .agents.git import GitAgent
from .agents.memory import MemoryAgent
from .agents.planner import PlannerAgent
from .agents.research import ResearchAgent
from .agents.reviewer import ReviewerAgent
from .agents.terminal import TerminalAgent

# Sprint 2.2/2 + 2.3 — Registry + Capabilities + Lifecycle + Manifest
from .registry import (
    ACTION_TO_CAPABILITY,
    AGENT_NAME_TO_CAPABILITIES,
    AgentManifest,
    AgentRecord,
    AgentRegistry,
    BackwardCompatibilityAdapter,
    Capability,
    CapabilityRegistry,
    CapabilityResolver,
    ExecutionResult,
    LifecycleHealth,
    LifecycleManager,
    LifecycleState,
    RegistryOrchestrator,
    ResolutionResult,
    ResolverWeights,
    can_transition_lifecycle,
    register_standard_capabilities,
    standard_manifests,
)

# Sprint 2.2 — Workflow engine
from .workflow.context import CodeSnippet, ContextManager, FileSnapshot, SharedMessage
from .workflow.dag import DAG, ValidationResult
from .workflow.engine import FailoverPolicy, WorkflowEngine, WorkflowExecutionResult
from .workflow.enums import (
    FailoverAction,
    StepCriticality,
    StepStatus,
    WorkflowEvent,
    WorkflowStatus,
)
from .workflow.event_bus import Event, EventBus
from .workflow.load_balancer import AgentMetrics, LoadBalancer, LoadBalancerWeights
from .workflow.models import Step, Workflow
from .workflow.orchestrator import WorkflowOrchestrator
from .workflow.observability import Histogram, MetricsCollector
from .workflow.registry_scheduler import RegistryAwareScheduler
from .workflow.scheduler import ScheduleDecision, Scheduler, SchedulerConfig

__all__ = [
    # Base
    "BaseAgent",
    "HealthReport",
    # Config
    "MultiAgentConfig",
    "RetryPolicy",
    # Enums
    "AgentStatus",
    "HealthState",
    "MessageType",
    "Permission",
    "Priority",
    "TaskStatus",
    # Logs
    "AgentLogger",
    "LogEntry",
    # Memory
    "AgentMemory",
    "MemoryEntry",
    # Messages
    "Message",
    "MessageBus",
    # Orchestrator
    "AgentOrchestrator",
    # Queue
    "PriorityQueue",
    "Task",
    # Concrete agents
    "CoderAgent",
    "GitAgent",
    "MemoryAgent",
    "PlannerAgent",
    "ResearchAgent",
    "ReviewerAgent",
    "TerminalAgent",
    # Sprint 2.2/2 + 2.3 — Registry + Capabilities + Lifecycle + Manifest
    "ACTION_TO_CAPABILITY",
    "AGENT_NAME_TO_CAPABILITIES",
    "AgentManifest",
    "AgentRecord",
    "AgentRegistry",
    "BackwardCompatibilityAdapter",
    "Capability",
    "CapabilityRegistry",
    "CapabilityResolver",
    "ExecutionResult",
    "LifecycleHealth",
    "LifecycleManager",
    "LifecycleState",
    "RegistryOrchestrator",
    "ResolutionResult",
    "ResolverWeights",
    "can_transition_lifecycle",
    "register_standard_capabilities",
    "standard_manifests",
    # Sprint 2.2 — Workflow
    "AgentMetrics",
    "CodeSnippet",
    "ContextManager",
    "DAG",
    "Event",
    "EventBus",
    "FailoverAction",
    "FailoverPolicy",
    "FileSnapshot",
    "Histogram",
    "LoadBalancer",
    "LoadBalancerWeights",
    "MetricsCollector",
    "RegistryAwareScheduler",
    "ScheduleDecision",
    "Scheduler",
    "SchedulerConfig",
    "SharedMessage",
    "Step",
    "StepCriticality",
    "StepStatus",
    "ValidationResult",
    "Workflow",
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowExecutionResult",
    "WorkflowOrchestrator",
    "WorkflowStatus",
]
