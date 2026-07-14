# Sprint 2.4 — Architecture Audit Report

## Executive Summary

Sprint 2.4 introduces a **capability-based Workflow Engine** that integrates with the existing Agent Registry, Capability Resolver, and Lifecycle Manager. The existing Sprint 2.2 WorkflowEngine takes direct `BaseAgent` instances; Sprint 2.4 creates a new **TaskPipelineEngine** that resolves agents by capability through the registry.

## Current Architecture

### Existing Components (NOT MODIFIED)

| Component | Location | Status |
|---|---|---|
| `Workflow` / `Step` | `workflow/models.py` | Stable (Sprint 2.2) |
| `WorkflowEngine` | `workflow/engine.py` | Stable (Sprint 2.2) |
| `DAG` | `workflow/dag.py` | Stable (Sprint 2.2) |
| `AgentRegistry` | `registry/agent_registry.py` | Stable (Sprint 2.2/2.3) |
| `CapabilityResolver` | `registry/resolver.py` | Stable (Sprint 2.2) |
| `LifecycleManager` | `registry/lifecycle.py` | Stable (Sprint 2.3) |
| `RegistryOrchestrator` | `registry/orchestrator.py` | Stable (Sprint 2.3) |

### New Components (Sprint 2.4)

| File | Purpose |
|---|---|
| `workflow/pipeline_models.py` | `TaskRequest`, `TaskResult`, `WorkflowStepDef`, `StepRetryPolicy`, `PipelineWorkflow` |
| `workflow/task_pipeline_engine.py` | `TaskPipelineEngine` — capability-based engine |

### Integration Pattern

```
WorkflowStep.capability_required
    → CapabilityResolver.resolve(capability)
    → AgentRegistry → AgentRecord → BaseAgent
    → BaseAgent.execute(task)
    → TaskResult produced
    → Context updated for dependent steps
```

## Test Results

- **Before**: 791 tests passing
- **After**: 864 tests passing (+73 new)
- **Existing tests broken**: 0