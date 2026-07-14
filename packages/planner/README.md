# Liz Planner — Intelligent Task Planner & Execution Engine

Sprint 2.8 — Intelligent Task Planner & Execution Engine for Liz Coder Plus.

## Overview

The `liz-planner` package decomposes complex user requests into optimized
execution plans represented as a Directed Acyclic Graph (DAG) of `Task`
objects. It assigns each task to the best available agent using a
multi-signal scoring algorithm and provides a full `Scheduler` that
executes the plan respecting dependencies, priorities, retries, and
recovery policies.

## Architecture

```
User Request
    │
    ▼
TaskPlanner.create_plan()
    │   ├── TaskDecomposer     → split into subtasks
    │   ├── DependencyAnalyzer → infer dependencies
    │   ├── CostEstimator      → time / tokens / cost / difficulty
    │   └── CapabilityMatcher   → assign agents
    │
    ▼
ExecutionPlan  (tasks + DAG + parallel groups + execution order)
    │
    ▼
Scheduler  (priority + parallel + retries + recovery)
    │
    ▼
Agents  (Multi-Agent Orchestrator)
    │
    ▼
Semantic Memory  (retrieve similar past plans)
```

## Public API

```python
from planner import (
    Task,
    TaskStatus,
    TaskPriority,
    Difficulty,
    ExecutionPlan,
    DAG,
    Scheduler,
    SchedulerConfig,
    TaskPlanner,
    PlannerConfig,
    TaskDecomposer,
    DependencyAnalyzer,
    CapabilityMatcher,
    CostEstimator,
    EstimatorConfig,
    # Exceptions
    PlannerError,
    CycleDetectedError,
    TaskNotFoundError,
    PlanValidationError,
)
```

## Integration

The planner integrates with the existing Sprint 2.1–2.7 modules:

- **`AgentRegistry`** (Sprint 2.2-2) — agent discovery by capability.
- **`CapabilityResolver`** (Sprint 2.2-2) — weighted agent selection.
- **`AgentManifest`** (Sprint 2.3) — declarative agent contracts.
- **`MultiAgentOrchestrator`** (Sprint 2.1) — agent execution.
- **`WorkflowEngine`** (Sprint 2.2) — workflow execution pipeline.
- **`SemanticRetrievalEngine`** (Sprint 2.7) — retrieve similar past plans.

The integration is **optional**: the planner works standalone with
in-memory data structures. When a registry/resolver/memory client is
provided, it uses them transparently.

## Compatibility

- Python 3.11+ (target 3.12 per sprint spec).
- No breaking changes to existing modules.
- New package: `packages/planner/`.
- Tests live in `tests/planner/`.
