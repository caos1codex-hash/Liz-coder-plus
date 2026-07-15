"""Planner package — Intelligent Task Planning Engine.

Sprint 3.1 + 3.2 — Liz Coder Plus v0.1.1.

This package provides structural planning and rule-based plan generation.
It creates, validates, serializes, and analyses execution plans
without performing any actual task execution.

Sprint 3.2 additions:
    - Goal-to-plan generation via ``TaskPlanner.create_from_goal()``
    - Task analysis (type, complexity, agent suggestion)
    - Rule engine for agent detection
    - Plan templates (development, bugfix, research, docs, etc.)
    - Complexity estimation
"""

# -- Sprint 3.2 new modules -------------------------------------------------
from .analyzer import TaskAnalyzer
from .complexity import ComplexityEstimator, ComplexityLevel
from .context import PlanningContext

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
from .generator import PlanGenerator

# -- interfaces -------------------------------------------------------------
from .interfaces import IMetrics, IPlanner, ISerializer, IValidator

# -- implementations --------------------------------------------------------
from .metrics import PlannerMetrics

# -- models -----------------------------------------------------------------
from .models import PlanMetrics, PlanStep, TaskPlan
from .planner import TaskPlanner
from .rules import PlannerRule, PlannerRuleEngine, create_default_rule_engine
from .serializer import PlanSerializer
from .templates import (
    AnalysisTemplate,
    BugFixTemplate,
    DevelopmentTaskTemplate,
    DevOpsTemplate,
    DocumentationTemplate,
    GeneralTemplate,
    PlanTemplate,
    ResearchTemplate,
    TestingTemplate,
    all_templates,
    get_template,
)
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
]
