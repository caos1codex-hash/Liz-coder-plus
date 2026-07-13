# Changelog — Liz Coder Plus

All notable changes to this project are documented here.

## [Unreleased] — Sprint 2.2 — Collaborative Multi-Agent Execution

### Added
- **Workflow Engine** (`packages/multiagent/src/multiagent/workflow/`):
  - `Workflow` y `Step` con todos los campos de la especificación.
  - `DAG` con `validate()` (ciclos, dependencias rotas, huérfanos),
    `topological_order()`, `ready_steps()`, `ancestors()`, `descendants()`.
  - `EventBus` separado del `MessageBus` (pub/sub de eventos del sistema)
    con historial, métricas, filtros y aislamiento de errores.
  - `ContextManager` para compartir objetivos, archivos (SHA-256),
    snippets de código, variables, mensajes y memoria entre agentes.
    Soporta `transfer_context` entre agentes.
  - `Scheduler` inteligente con `max_parallel_steps` y
    `max_parallel_agents`. Respeta `step.agent` explícito.
  - `LoadBalancer` con scoring ponderado (estado, CPU, RAM, cola,
    especialidad, historial).
  - `WorkflowEngine` con ejecución paralela, failover (retry/reassign/
    skip/cancel), propagación de SKIP a dependientes, reset de agentes.
  - `FailoverPolicy` configurable.
  - `WorkflowOrchestrator` que combina `AgentOrchestrator` (Sprint 2.1)
    + `WorkflowEngine` (Sprint 2.2). Métodos `delegate`, `request_help`,
    `transfer_context`, `wait_for`, `cancel_workflow`, `resume_workflow`.
  - `MetricsCollector` suscrito al EventBus con histograms (p50/p95/p99)
    de workflow_duration, step_duration, queue_time, y contadores de
    parallelism, agent_usage, retry_count, success_rate.
  - API REST (FastAPI): `/workflows`, `/workflows/{id}`, `/steps`,
    `/events`, `/metrics/workflows`, `/metrics/agents`.
- **PlannerAgent extendido**: con `op=plan_workflow` genera un Workflow
  completo con DAG y estimaciones de tokens, duración y parallelismo.
- **Documentación**: `docs/sprint-2.2-collaborative-agents-report.md` y
  `docs/sprint-2.2-diagrams.md` (10 diagramas Mermaid).
- **Tests**: 98 nuevos (DAG, models, EventBus, LoadBalancer, Scheduler,
  Engine con failover/parallel/context).

### Fixed
- `Workflow.ready_steps()` ahora incluye steps en READY (vienen de retry).
- `WorkflowEngine` resetea el agente a IDLE tras failover retry/reassign.
- Skip de un step se propaga en cadena a sus dependientes.
- `Scheduler` respeta `step.agent` explícito; si está excluido, usa LoadBalancer.
- `EventBus` usa `hasattr(type, 'value')` para ser robusto a re-imports del enum.
- Permitida la transición `FAILED → SKIPPED` en `StepStatus`.

### Compatibilidad
- Sprint 2.1: 130/130 tests siguen pasando.
- Sprint 1: 56+ tests verificados siguen pasando.

## [Unreleased] — Sprint 2.1 — Multi-Agent Orchestrator

### Added
- **New package `packages/multiagent`** — arquitectura profesional basada en agentes.
- `BaseAgent` (abstracta) con id/name/description/status/permissions/memory/tools/objectives
  y lifecycle `execute/stop/pause/resume/health/serialize`.
- `AgentOrchestrator` con `register_agent/remove_agent/get_agent/dispatch/
  broadcast/pauseAll/resumeAll/health/metrics/start_heartbeat/shutdown`.
- `MessageBus` y `Message` con tipos REQUEST/RESPONSE/EVENT/ERROR/SYSTEM,
  prioridades, broadcast, pending, isolation de errores.
- `PriorityQueue` y `Task` con estados queued/running/paused/completed/
  failed/cancelled, retry, ordenamiento por prioridad + FIFO.
- `AgentMemory` con memoria corta (deque), larga (LRU) y embeddings
  (cosine similarity sin numpy).
- `AgentLogger` con fases start/end/error/tool/metric y tracking de
  duration_ms, memory_kb, tokens_prompt, tokens_completion, tools_used.
- `MultiAgentConfig` inmutable (max_agents, timeouts, retry con backoff,
  heartbeat, capacidades).
- 7 agentes concretos: `PlannerAgent`, `CoderAgent`, `ReviewerAgent`,
  `ResearchAgent`, `TerminalAgent`, `GitAgent`, `MemoryAgent`.
- `HealthReport` con estados alive/busy/idle/error/dead.
- 115 tests unitarios cubriendo todos los componentes (enums, mensajes,
  cola, base, orchestrator, agentes concretos, inter-agent messaging).
- Documentación: `docs/sprint-2.1-multi-agent-report.md` y
  `docs/sprint-2.1-diagrams.md` (Mermaid).
- Sección 12 en `docs/architecture.md` describiendo la nueva capa.

### Compatibilidad
- No se reemplaza código de Sprint 1: `Orchestrator`, `BaseAgent` v1,
  `TaskManager`, `EventBus` coexisten sin cambios.
- Los 43+ tests de Sprint 1 verificados siguen pasando.

## [0.3.0] — 2026-07-14

### Sprint 1.10 — Hardening, Optimización, Validación Final

#### Added
- Pre-compiled regex patterns in Planner (eliminates re-compilation per request)
- StreamingManager: StringIO-based content accumulation (O(n) vs O(n²))
- ToolExecutor: cached inspect.signature per tool instance
- Scheduler: lexicographic ISO comparison for tick scheduling
- ModelManager: module-level provider class map
- Pipeline: correct duration_ms calculation in error paths

#### Changed
- Pipeline: fixed duration_ms bug (was returning absolute timestamp instead of duration)
- TerminalTool: dangerous command patterns now BLOCK execution (was only logging)
- ToolExecutor.cancel(): fixed fallback that could cancel unrelated tasks
- RecoveryManager: replaced `__import__()` with proper import

#### Removed
- 7 dead/unused files: backend EventBus stub, backend orchestrator stub, memory sqlite.py stub, memory models.py, shared types.py, shared models.py, shared enums.py
- Dead `_load_core()` function from liz_core.py
- Unused imports across 6 files (shlex, asyncio, Awaitable, re, etc.)

#### Security
- TerminalTool: blocking dangerous patterns (rm -rf /, mkfs, dd if=, etc.)
- ToolExecutor: cancel no longer affects unrelated executions
- Pipeline: duration_ms no longer leaks timing info

#### Tests
- 409/434 unit tests passing (94.2%)
- 0 new regressions from Sprint 1.10 changes
- 25 pre-existing permission test failures (TD-002, Sprint 2)

## [0.2.1] — Sprint 1.9

### LLM Engine
- 5 LLM providers: OpenAI, Anthropic, Google, Ollama, OpenRouter
- ModelManager with registration, activation, health checks
- ContextManager with token budgeting, trimming, summarization
- PromptPipeline for unified prompt construction
- StreamingManager with cancellation, backpressure, callbacks
- 64 tests for LLM subsystem

## [0.2.0] — Sprint 1.8

### Integration & Persistence
- Task and Workflow persistence (SQLite)
- Scheduler with interval and one-shot scheduling
- Scheduler persistence and recovery
- Integration audit and fixes

## [0.1.4] — Sprint 1.7

### Core Execution
- Unified ExecutionPipeline (11 stages)
- Planner (heuristic, rule-based)
- TaskManager with state machine
- WorkflowManager with DAG execution
- Observability (MetricsCollector, StructuredLogger, AuditRecorder)

## [0.1.3] — Sprint 1.6

### Tools & Permissions
- Tool framework with BaseTool, ToolRegistry, ToolExecutor
- FileTool, SystemTool, TerminalTool
- PermissionService with levels and audit logging

## [0.1.2] — Sprint 1.5

### Agent Framework
- AgentRouter with agent registration and selection
- BaseAgent with lifecycle management
- Agent validation

## [0.1.1] — Sprint 1.4

### Memory & Configuration
- Persistent memory with SQLite
- ConversationRepository, TaskRepository, WorkflowRepository
- Configuration system (development/production JSON)

## [0.1.0] — Sprint 1.3

### Foundation
- Core orchestrator with EventBus
- SessionManager with conversation tracking
- WebSocketManager for real-time communication
- Backend FastAPI scaffolding
- Desktop WinUI 3 scaffolding