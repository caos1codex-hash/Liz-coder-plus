"""Planner package — Intelligent Task Planning Engine.

Sprint 3.1 + 3.2 + 3.3 + 3.4 — Liz Coder Plus v0.1.1.

This package provides structural planning and rule-based plan generation.
It creates, validates, serializes, and analyses execution plans
without performing any actual task execution.

Sprint 3.2 additions:
    - Goal-to-plan generation via ``TaskPlanner.create_from_goal()``
    - Task analysis (type, complexity, agent suggestion)
    - Rule engine for agent detection
    - Plan templates (development, bugfix, research, docs, etc.)
    - Complexity estimation

Sprint 3.3 additions:
    - Agent-aware planning via ``TaskPlanner.create_agent_aware_plan()``
    - Agent planning context (registry snapshot)
    - Capability mapper (plan needs → registry capabilities)
    - Agent selector (scoring and ranking)
    - Agent assignment model
    - Integration with Sprint 2 AgentRegistry

Sprint 3.4 additions:
    - PlannerExecutor — bridges TaskPlan → Workflow → real agent
      execution via the existing WorkflowEngine + RegistryAwareScheduler.
    - Planner-level event names (``planner.plan.*`` / ``planner.step.*``)
      published on the workflow EventBus.
    - PlanExecutionResult dataclass summarising a run.
    - Optional plan persistence via the memory package's PlanRepository
      (see ``packages/memory/src/memory/repositories/plan_repository.py``).
"""

# -- Sprint 3.4 new modules -------------------------------------------------
from .executor import PlanExecutionResult, PlannerExecutor
from .executor_events import (
    ALL_EVENTS,
    PLAN_CANCELLED,
    PLAN_COMPLETED,
    PLAN_FAILED,
    PLAN_STARTED,
    PLAN_SUBMITTED,
    PLAN_TRANSLATED,
    STEP_FINISHED,
    STEP_STARTED,
    STEP_TRANSLATED,
)
# -- Sprint 3.3 new modules -------------------------------------------------
from .agent_context import AgentInfo, AgentPlanningContext
from .agent_selector import AgentSelector
# -- Sprint 3.2 new modules -------------------------------------------------
from .analyzer import TaskAnalyzer
from .assignment import AgentAssignment
from .capability_mapper import CapabilityMapper
from .complexity import ComplexityEstimator, ComplexityLevel
from .context import PlanningContext
# -- enums ------------------------------------------------------------------
from .enums import (PlanStatus, Priority, StepStatus, can_transition_plan,
                    can_transition_step)
# -- exceptions -------------------------------------------------------------
from .exceptions import (AgentNotFoundError, AgentPlanningError,
                         AssignmentError, CapabilityMissingError,
                         CircularDependencyError, DependencyError,
                         DuplicateStepError, InvalidStatusError,
                         PlannerException, PlanValidationError,
                         SerializationError)
from .generator import PlanGenerator
from .integration import AgentAwarePlanner
# -- interfaces -------------------------------------------------------------
from .interfaces import IMetrics, IPlanner, ISerializer, IValidator
# -- implementations --------------------------------------------------------
from .metrics import PlannerMetrics
# -- models -----------------------------------------------------------------
from .models import PlanMetrics, PlanStep, TaskPlan
from .planner import TaskPlanner
from .rules import PlannerRule, PlannerRuleEngine, create_default_rule_engine
from .serializer import PlanSerializer
from .templates import (AnalysisTemplate, BugFixTemplate,
                        DevelopmentTaskTemplate, DevOpsTemplate,
                        DocumentationTemplate, GeneralTemplate, PlanTemplate,
                        ResearchTemplate, TestingTemplate, all_templates,
                        get_template)
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
    "ComplexityLevel",
    # exceptions
    "PlannerException",
    "PlanValidationError",
    "SerializationError",
    "DependencyError",
    "CircularDependencyError",
    "DuplicateStepError",
    "InvalidStatusError",
    # Sprint 3.3 exceptions
    "AgentPlanningError",
    "AgentNotFoundError",
    "CapabilityMissingError",
    "AssignmentError",
    # interfaces
    "IPlanner",
    "IValidator",
    "ISerializer",
    "IMetrics",
    # Sprint 3.1 implementations
    "TaskPlanner",
    "PlanValidator",
    "PlanSerializer",
    "PlannerMetrics",
    # Sprint 3.2 implementations
    "PlanningContext",
    "ComplexityEstimator",
    "TaskAnalyzer",
    "PlannerRule",
    "PlannerRuleEngine",
    "PlanGenerator",
    "PlanTemplate",
    "DevelopmentTaskTemplate",
    "BugFixTemplate",
    "ResearchTemplate",
    "DocumentationTemplate",
    "AnalysisTemplate",
    "DevOpsTemplate",
    "TestingTemplate",
    "GeneralTemplate",
    "get_template",
    "all_templates",
    "create_default_rule_engine",
    # Sprint 3.3 implementations
    "AgentInfo",
    "AgentPlanningContext",
    "CapabilityMapper",
    "AgentSelector",
    "AgentAssignment",
    "AgentAwarePlanner",
    # Sprint 3.4 implementations
    "PlannerExecutor",
    "PlanExecutionResult",
    # Sprint 3.4 event constants
    "PLAN_SUBMITTED",
    "PLAN_TRANSLATED",
    "PLAN_STARTED",
    "PLAN_COMPLETED",
    "PLAN_FAILED",
    "PLAN_CANCELLED",
    "STEP_TRANSLATED",
    "STEP_STARTED",
    "STEP_FINISHED",
    "ALL_EVENTS",
]
