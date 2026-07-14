# Module Responsibility Map — Liz Coder Plus

**Sprint 2.10 — Final Architecture Audit**  
**Status**: Canonical — defines which module owns which responsibility.  
**Last updated**: 2026-07-14

This document maps every module in the monorepo to its **single
responsibility**. When adding a new feature, use this map to decide
where it belongs. If a feature spans multiple responsibilities, it
belongs in an **integration module** (not in a leaf module).

---

## 1. Package Overview

```
packages/
├── core/           ← Backend orchestrator (WebSocket, sessions, pipeline)
├── multiagent/     ← Multi-agent runtime (agents, registry, workflow, semantic)
├── communication/  ← Agent messaging + collaboration (Sprint 2.9)
├── planner/        ← Task planning + scheduling (Sprint 2.8)
├── memory/         ← SQLite persistence (conversations, tasks, workflows)
├── llm/            ← LLM provider abstraction (OpenAI, Anthropic, Google, etc.)
├── tools/          ← Tool system (file, terminal, system tools)
├── agents/         ← Lightweight Agent Protocol (core orchestrator agents)
└── shared/         ← Shared types (WebSocket models, utils)
```

---

## 2. Responsibility Matrix

| Concern | Owner package | Owner module | Key classes |
|---------|--------------|--------------|-------------|
| **WebSocket server** | `core` | `src/ws_manager.py`, `src/ws_connection.py` | `WebSocketManager`, `WebSocketConnection` |
| **Session management** | `core` | `src/session_manager.py` | `SessionManager` |
| **Execution pipeline** | `core` | `src/pipeline.py` | `ExecutionPipeline` |
| **Core event bus** | `core` | `src/event_bus.py` | `EventBus` (lightweight pub/sub) |
| **Core task model** | `core` | `src/task.py` | `Task`, `TaskState`, `TaskPriority`, `TaskManager` |
| **Core workflow (legacy)** | `core` | `src/workflow.py` | `Workflow`, `WorkflowManager`, `WorkflowBuilder` |
| **Core scheduler (time-based)** | `core` | `src/scheduler.py` | `Scheduler` (ONCE/INTERVAL/CRON jobs) |
| **Core planner (legacy heuristic)** | `core` | `src/planner.py` | `Planner`, `Plan`, `SubTask` |
| **Core observability** | `core` | `src/observability.py` | `MetricsCollector`, `StructuredLogger`, `AuditRecorder` |
| **Tool executor** | `core` | `src/tool_executor.py` | `ToolExecutor` |
| **Agent router** | `core` | `src/agent_router.py` | `AgentRouter`, `EchoAgent` |
| **Recovery** | `core` | `src/recovery.py` | `RecoveryManager` |
| **BaseAgent (multiagent)** | `multiagent` | `multiagent/base.py` | `BaseAgent`, `HealthReport` |
| **Agent orchestrator** | `multiagent` | `multiagent/orchestrator.py` | `AgentOrchestrator` |
| **MessageBus (wire format)** | `multiagent` | `multiagent/messages.py` | `Message`, `MessageBus` |
| **Task queue** | `multiagent` | `multiagent/queue.py` | `Task`, `PriorityQueue` |
| **Agent enums** | `multiagent` | `multiagent/enums.py` | `AgentStatus`, `Priority`, `TaskStatus`, `HealthState` |
| **Agent config** | `multiagent` | `multiagent/config.py` | `MultiAgentConfig` |
| **Agent memory (per-agent)** | `multiagent` | `multiagent/memory.py` | `AgentMemory` |
| **AgentRegistry** | `multiagent` | `multiagent/registry/agent_registry.py` | `AgentRegistry`, `AgentRecord` |
| **AgentManifest** | `multiagent` | `multiagent/registry/manifest.py` | `AgentManifest`, `standard_manifests()` |
| **CapabilityResolver** | `multiagent` | `multiagent/registry/resolver.py` | `CapabilityResolver`, `ResolutionResult` |
| **Capability system** | `multiagent` | `multiagent/registry/capability.py` | `Capability`, `CapabilityRegistry` |
| **Lifecycle manager** | `multiagent` | `multiagent/registry/lifecycle.py` | `LifecycleManager`, `LifecycleState` |
| **Registry orchestrator** | `multiagent` | `multiagent/registry/orchestrator.py` | `RegistryOrchestrator`, `ExecutionResult` |
| **Backward compat adapter** | `multiagent` | `multiagent/registry/adapter.py` | `BackwardCompatibilityAdapter` |
| **Workflow engine** | `multiagent` | `multiagent/workflow/engine.py` | `WorkflowEngine`, `FailoverPolicy` |
| **Workflow models** | `multiagent` | `multiagent/workflow/models.py` | `Workflow`, `Step` |
| **Workflow DAG** | `multiagent` | `multiagent/workflow/dag.py` | `DAG`, `ValidationResult` |
| **Workflow scheduler (step-based)** | `multiagent` | `multiagent/workflow/scheduler.py` | `Scheduler` |
| **Workflow event bus** | `multiagent` | `multiagent/workflow/event_bus.py` | `Event`, `EventBus` (rich, with history) |
| **Workflow context** | `multiagent` | `multiagent/workflow/context.py` | `ContextManager`, `FileSnapshot`, `CodeSnippet` |
| **Workflow load balancer** | `multiagent` | `multiagent/workflow/load_balancer.py` | `LoadBalancer` |
| **Workflow orchestrator** | `multiagent` | `multiagent/workflow/orchestrator.py` | `WorkflowOrchestrator` |
| **Workflow observability** | `multiagent` | `multiagent/workflow/observability.py` | `MetricsCollector`, `Histogram` |
| **Semantic memory (Sprint 2.7)** | `multiagent` | `multiagent/semantic/` | `SemanticRetrievalEngine`, `EmbeddingProvider`, `VectorStore`, `RankingEngine` |
| **Memory context (Sprint 2.6)** | `multiagent` | `multiagent/memctx/` | `MemoryEngine`, `ContextEngine` |
| **Concrete agents** | `multiagent` | `multiagent/agents/` | `PlannerAgent`, `CoderAgent`, `ReviewerAgent`, `ResearchAgent`, `TerminalAgent`, `GitAgent`, `MemoryAgent` |
| **Plugin system (Sprint 2.5)** | `multiagent` | `multiagent/plugins/` | `PluginManager`, `PluginLoader`, `PluginEventBus` |
| **AgentMessage protocol** | `communication` | `communication/protocol.py` | `AgentMessage`, `MessageType`, `MessagePriority` |
| **AgentMessageBus** | `communication` | `communication/bus.py` | `AgentMessageBus`, `BusConfig` |
| **CollaborationManager** | `communication` | `communication/collaboration.py` | `CollaborationManager`, `Collaboration`, `CollaborationStatus` |
| **Message repository** | `communication` | `communication/repository.py` | `MessageRepository`, `InMemoryMessageRepository` |
| **Communication integration** | `communication` | `communication/integration.py` | `RegistryAdapter`, `OrchestratorAdapter`, `build_integrated_*()` |
| **TaskPlanner** | `planner` | `planner/planner.py` | `TaskPlanner`, `PlannerConfig` |
| **Planner Task model** | `planner` | `planner/task.py` | `Task`, `TaskStatus`, `TaskPriority` |
| **Planner DAG** | `planner` | `planner/graph.py` | `DAG` (plan-specific) |
| **ExecutionPlan** | `planner` | `planner/execution_plan.py` | `ExecutionPlan` |
| **Planner scheduler (plan-based)** | `planner` | `planner/scheduler.py` | `Scheduler`, `SchedulerConfig` |
| **TaskDecomposer** | `planner` | `planner/task_decomposer.py` | `TaskDecomposer`, `DecomposerConfig` |
| **DependencyAnalyzer** | `planner` | `planner/dependency_analyzer.py` | `DependencyAnalyzer` |
| **CostEstimator** | `planner` | `planner/estimator.py` | `CostEstimator`, `Estimate` |
| **CapabilityMatcher** | `planner` | `planner/capability_matcher.py` | `CapabilityMatcher`, `AgentInfo`, `AgentRegistryAdapter` |
| **Planner integration** | `planner` | `planner/integration.py` | `MultiAgentRegistryAdapter`, `SemanticMemoryAdapter`, `WorkflowEngineAdapter` |
| **SQLite database** | `memory` | `memory/database.py` | `DatabaseManager` |
| **Memory manager** | `memory` | `memory/manager.py` | `MemoryManager` |
| **Conversation repository** | `memory` | `memory/repositories/conversation_repository.py` | `ConversationRepository` |
| **Task repository** | `memory` | `memory/repositories/task_repository.py` | `TaskRepository` |
| **Workflow repository** | `memory` | `memory/repositories/workflow_repository.py` | `WorkflowRepository` |
| **Execution repository** | `memory` | `memory/repositories/execution_repository.py` | `ExecutionRepository` |
| **LLM manager** | `llm` | `llm/manager.py` | `ModelManager` |
| **LLM providers** | `llm` | `llm/providers/` | `OpenAIProvider`, `AnthropicProvider`, `GoogleProvider`, `OllamaProvider`, `OpenRouterProvider` |
| **LLM context** | `llm` | `llm/context/manager.py` | `ContextManager` |
| **LLM streaming** | `llm` | `llm/streaming/manager.py` | `StreamingManager` |
| **LLM prompt pipeline** | `llm` | `llm/prompt/pipeline.py` | `PromptPipeline` |
| **Tool base** | `tools` | `tools/base.py` | `BaseTool` |
| **Tool registry** | `tools` | `tools/registry.py` | `ToolRegistry` |
| **File tool** | `tools` | `tools/tools/file_tool.py` | `FileTool` |
| **Terminal tool** | `tools` | `tools/tools/terminal_tool.py` | `TerminalTool` |
| **System tool** | `tools` | `tools/tools/system_tool.py` | `SystemTool` |
| **Agent Protocol (lightweight)** | `agents` | `agents/base.py` | `Agent` Protocol |
| **Agent registry (lightweight)** | `agents` | `agents/registry.py` | `AgentRegistry` |
| **WebSocket models** | `shared` | `shared/ws_models.py` | Pydantic models for WS messages |
| **Shared utils** | `shared` | `shared/utils.py` | Helpers |

---

## 3. Module Boundaries (Cross-Package Imports)

### 3.1 Allowed (lazy) imports

```
core         ──lazy──► memory        (TYPE_CHECKING + function-local)
planner      ──lazy──► multiagent    (integration.py only)
communication──lazy──► multiagent    (bus.py, protocol.py, integration.py)
apps/backend ──path──►  core, memory, shared  (via liz_core.py shim)
```

### 3.2 Forbidden imports

```
multiagent   ──✗──►  planner, communication, core    (multiagent is a leaf)
memory       ──✗──►  any other package                (memory is a leaf)
llm          ──✗──►  any other package                (llm is a leaf)
tools        ──✗──►  any other package                (tools is a leaf)
shared       ──✗──►  any other package                (shared is a leaf)
agents       ──✗──►  core (currently eager — known debt, to fix)
```

### 3.3 No circular imports

The audit confirmed **zero runtime circular imports**. All cross-package
dependencies use:
- `TYPE_CHECKING` blocks (for type hints only), or
- Function-local imports (deferred until runtime), or
- `try/except ImportError` guards (for optional dependencies).

---

## 4. Known Duplications (Conscious, Documented)

The audit found these duplications. They are **known and documented**
but NOT merged in this sprint (would require breaking changes). Future
sprints should address them:

### 4.1 Three `Task` classes

| Module | Class | Unit | Status enum |
|--------|-------|------|-------------|
| `core/src/task.py` | `Task` | Core orchestrator task | `TaskState` (PENDING/READY/RUNNING/WAITING/COMPLETED/FAILED/CANCELLED) |
| `multiagent/queue.py` | `Task` | Queue task | `TaskStatus` (QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED/PAUSED) |
| `planner/task.py` | `Task` | Planned task | `TaskStatus` (PENDING/READY/RUNNING/WAITING/COMPLETED/FAILED/CANCELLED/SKIPPED) |

**Recommendation**: extract a common `TaskBase` into `packages/shared/`
in a future sprint. Adapters translate between the three.

### 4.2 Three `Scheduler` classes

| Module | Class | Schedules |
|--------|-------|-----------|
| `core/src/scheduler.py` | `Scheduler` | Time-based jobs (ONCE/INTERVAL/CRON) |
| `multiagent/workflow/scheduler.py` | `Scheduler` | Workflow steps (DAG-aware) |
| `planner/scheduler.py` | `Scheduler` | Planned tasks (ExecutionPlan-aware) |

**Recommendation**: rename to `TimeScheduler`, `StepScheduler`,
`PlanScheduler` to avoid reader confusion.

### 4.3 Two `EventBus` classes

| Module | Class | API |
|--------|-------|-----|
| `core/src/event_bus.py` | `EventBus` | `subscribe(event_type, handler)`, `publish(event_type, payload)` |
| `multiagent/workflow/event_bus.py` | `EventBus` | `subscribe(handler, *, event_type, filter_fn)`, `publish(Event)` with history + sequence |

**Recommendation**: standardise on `multiagent.workflow.EventBus` (richer).
The `communication.AgentMessageBus` already bridges between the two.

### 4.4 Four `*Orchestrator` classes

| Module | Class | Scope |
|--------|-------|-------|
| `core/orchestrator.py` | `Orchestrator` | Backend coordinator (WS, sessions, tools) |
| `multiagent/orchestrator.py` | `AgentOrchestrator` | Multi-agent runtime |
| `multiagent/workflow/orchestrator.py` | `WorkflowOrchestrator` | Workflow execution |
| `multiagent/registry/orchestrator.py` | `RegistryOrchestrator` | Registry-aware execution |

**Recommendation**: keep as-is (they serve different layers) but document
the layering clearly.

### 4.5 Two `Planner` classes

| Module | Class | Style |
|--------|-------|-------|
| `core/src/planner.py` | `Planner` | Heuristic regex-based (legacy) |
| `planner/planner.py` | `TaskPlanner` | Sophisticated (decomposer + analyzer + estimator + matcher) |

**Recommendation**: deprecate `core.Planner` in a future sprint; make it
a thin shim that delegates to `planner.TaskPlanner`.

### 4.6 Four memory subsystems

| Module | Purpose |
|--------|---------|
| `memory/manager.py` | SQLite persistence (conversations, tasks, workflows) |
| `multiagent/memory.py` | Per-agent in-memory store (AgentMemory) |
| `multiagent/memctx/` | Memory engine + context engine (Sprint 2.6) |
| `multiagent/semantic/` | Semantic retrieval (embeddings, vectors) (Sprint 2.7) |

**Recommendation**: long-term, consolidate into one memory package with
pluggable backends. Short-term, document which subsystem to use for
which use-case.

---

## 5. The `src` Namespace Collision (Known Debt)

**Critical packaging issue**: every package's source directory is named
`src/`, and the `__init__.py` makes `src` itself the top-level package.
This means only one package's `src` can be on `sys.path` at a time.

**Workarounds in use**:
- `packages/core/liz_core.py` — a shim that saves/restores `sys.modules["src"]`.
- `apps/backend/src/api/main.py` — adds `packages/memory/` to `sys.path`.
- Each test module manipulates `sys.path` individually.

**Impact**: blocks `pip install -e .` for individual packages, breaks
IDE jump-to-definition, makes packages non-distributable.

**Recommendation**: rename each package's top-level dir to match its
published name (e.g. `packages/core/src/` → `packages/core/liz_core/`).
This is a **future sprint** task — too large for Sprint 2.10.

---

## 6. Integration Points

### 6.1 Planner → Multiagent

- `planner/integration.py` lazily imports `multiagent.registry.AgentRegistry`,
  `multiagent.semantic.SemanticRetrievalEngine`, `multiagent.workflow.models`.
- `MultiAgentRegistryAdapter` wraps `AgentRegistry` for the planner's
  `CapabilityMatcher`.
- `WorkflowEngineAdapter.to_workflow()` converts `ExecutionPlan` → `Workflow`.

### 6.2 Communication → Multiagent

- `communication/bus.py` lazily imports `multiagent.messages.MessageBus`.
- `AgentMessageBus` wraps `MessageBus` and adds persistence, TTL, tracing.
- `AgentMessage.to_message()` / `from_message()` bridge the wire format.
- Falls back to `_StandaloneBus` if `multiagent` is unavailable.

### 6.3 Communication → Memory

- `communication/repository.py` `MessageRepository` wraps
  `memory.repositories.ExecutionRepository` to persist messages as
  `execution_history` rows with `event_type="agent.message"`.
- No new SQLite schema required.

### 6.4 Core → Memory

- `core/src/orchestrator.py`, `task.py`, `workflow.py`, `session_manager.py`
  lazily import `memory.manager.MemoryManager` and repositories.
- Uses `TYPE_CHECKING` for type hints.

### 6.5 Backend → Core

- `apps/backend/src/api/main.py` uses the `liz_core.py` shim to import
  `Orchestrator`, `WebSocketManager`, etc.
- `apps/backend/src/api/ws_routes.py` adds `packages/memory/` to
  `sys.path` for `MemoryManager`.

---

## 7. Adding a New Feature — Decision Tree

```
New feature?
│
├── Is it a new agent?
│   └── YES → packages/multiagent/src/multiagent/agents/
│             Subclass multiagent.base.BaseAgent
│             Add a manifest in registry/manifest.py
│
├── Is it a new tool?
│   └── YES → packages/tools/src/tools/tools/
│             Subclass tools.base.BaseTool
│             Register in tools/registry.py
│
├── Is it a new LLM provider?
│   └── YES → packages/llm/src/llm/providers/
│             Subclass llm.providers.base.BaseProvider
│             Register in llm/manager.py
│
├── Is it a new memory backend?
│   └── YES → packages/memory/src/memory/
│             Add repository in repositories/
│             Wire into MemoryManager
│
├── Is it a new communication pattern?
│   └── YES → packages/communication/src/communication/
│             Add message type in protocol.py
│             Add handler in collaboration.py
│
├── Is it a new planning strategy?
│   └── YES → packages/planner/src/planner/
│             Add template in task_decomposer.py
│             Add strategy in planner.py
│
├── Is it a new workflow feature?
│   └── YES → packages/multiagent/src/multiagent/workflow/
│             Check if it belongs in engine.py, scheduler.py, or models.py
│
├── Is it a core backend feature (WS, sessions)?
│   └── YES → packages/core/src/
│             Check if it belongs in orchestrator.py, ws_manager.py, etc.
│
└── Does it span multiple packages?
    └── YES → Add an integration module (e.g. planner/integration.py)
              Use lazy imports
              Do NOT add cross-package imports to __init__.py
```
