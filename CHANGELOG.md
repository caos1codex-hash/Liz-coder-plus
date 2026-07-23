# Changelog — Liz Coder Plus

All notable changes to this project are documented here.

## [0.11.0] — 2026-07-23 — Sprint 5: Multi-Provider LLM Integration

### Sprint 5 — Proveedores LLM, REST API, Streaming Real, Desktop Enhancement

#### Resumen
Integración de múltiples proveedores LLM con auto-descubrimiento de modelos,
endpoints REST para chat y gestión, streaming real token-by-token vía WebSocket,
nuevas herramientas (búsqueda web, ejecución de código), y mejora significativa
de la aplicación desktop WinUI 3.

#### Nuevos Proveedores LLM
- **NVIDIA NIM** (`packages/llm/src/llm/providers/nvidia.py`)
  - API compatible con OpenAI en `integrate.api.nvidia.com/v1`
  - Auto-descubrimiento de modelos free via `/models` endpoint
  - Lista de prioridad: Llama 3.1 405B, Mistral Large, Qwen 72B, etc.
  - Capabilities predefinidas para 16+ modelos conocidos
  - Fallback a modelos hardcodeados si el discovery falla

- **DeepSeek** (`packages/llm/src/llm/providers/deepseek.py`)
  - Modelos: `deepseek-chat`, `deepseek-reasoner`, `deepseek-coder`
  - API compatible con OpenAI en `api.deepseek.com/v1`

- **Mistral AI** (`packages/llm/src/llm/providers/mistral.py`)
  - Modelos: `mistral-large-latest`, `codestral-latest`, `pixtral-large-latest`, etc.
  - Capabilities predefinidas para 8+ modelos conocidos

#### Cambios en Modelos y Manager
- `ModelProvider` enum extendido con `NVIDIA`, `DEEPSEEK`, `MISTRAL`
- `ModelManager._PROVIDER_CLASS_MAP` actualizado con 8 proveedores
- `providers/__init__.py` exporta todos los proveedores

#### Backend — Inicialización
- `apps/backend/src/api/main.py` — `_initialize_llm()` mejorado:
  - Registro automático de modelos NVIDIA NIM con discovery
  - Registro de modelos DeepSeek si `DEEPSEEK_API_KEY` está configurada
  - Registro de modelos Mistral si `MISTRAL_API_KEY` está configurada
  - Priority list de modelos NVIDIA free optimizada

#### Nuevos Endpoints REST
- `POST /api/chat` — Chat completo vía REST (pipeline full)
- `POST /api/chat/complete` — Completión directa LLM (sin pipeline)
- `GET /api/models/discover` — Descubrir modelos disponibles de todos los providers
- `ChatRequest` / `ChatResponse` models con Pydantic

#### Streaming Real LLM
- `Orchestrator.stream_message()` — Streaming token-by-token cuando LLMAgent está disponible
- `_stream_llm_real()` — Nuevo método que usa `provider.stream()` del LLM
- Fallback a word-chunk simulation cuando no hay LLM configurado
- Includes model/provider info en cada chunk envelope
- Persistencia de user y assistant turns durante streaming

#### Nuevas Herramientas
- **WebSearchTool** (`packages/tools/src/tools/web_search_tool.py`)
  - Búsqueda web via DuckDuckGo Lite (no requiere API key)
  - Fetch de URLs con limpieza de HTML
  - LOW permission level
- **CodeExecutionTool** (`packages/tools/src/tools/code_execution_tool.py`)
  - Ejecución de Python en subprocess sandboxed
  - Timeout configurable (default 30s, max 60s)
  - HIGH permission level
  - Restricción de red en el entorno de ejecución

#### Desktop WinUI 3
- `ChatViewModel.cs` — Full MVVM con CommunityToolkit.Mvvm
  - RelayCommand para Connect, Disconnect, Send, ToggleMode, ClearChat
  - Streaming support con append-to-last-message
  - INotifyPropertyChanged para data binding reactiva
- `MainWindow.xaml` — UI profesional:
  - Header con branding, status badge, controles de conexión
  - Toolbar con toggle de modo (Confirmar/Automático) y Limpiar
  - Área de mensajes con DataTemplate
  - Input area con TextBox + Button
- `MainWindow.xaml.cs` — Full code-behind:
  - Color-coded status badges (verde/naranja/rojo/gris)
  - Connection state tracking
  - Permission mode toggle
  - Auto-scroll en messages

#### Configuración
- `config/development.json` actualizado:
  - Provider default: `nvidia`
  - Model default: `meta/llama-3.1-405b-instruct`
  - Secciones `nvidia`, `deepseek`, `mistral` con URLs y env vars
  - Model roles: coding (deepseek-coder), reasoning (deepseek-reasoner)
- `.env.example` — Documentación de todas las variables de entorno

## [0.10.0] — 2026-07-17 — Sprint 4.2

### Sprint 4.2 — Tool Intelligence: Tool Selection Engine

#### Resumen
Motor de selección automática de herramientas que elige la mejor
herramienta disponible para cada tarea recibida por el Planner.
No ejecuta herramientas — solo selecciona.

#### Nuevos archivos (`packages/tools/src/`)
- **`exceptions.py`** — Jerarquía de excepciones:
  `ToolSelectionError`, `NoToolFoundError`, `AmbiguousSelectionError`,
  `RankingError`, `FilterError`.
- **`filters.py`** — 8 filtros independientes y reutilizables:
  `AvailabilityFilter`, `CapabilityFilter`, `ExecutionModeFilter`,
  `PermissionFilter`, `CostFilter`, `LatencyFilter`, `SafetyFilter`,
  `UserPreferenceFilter`. Incluye `SelectionContext`, `ExecutionMode`
  y `DEFAULT_FILTER_CHAIN`.
- **`ranking.py`** — Scoring multi-factor con pesos configurables:
  `RankingWeights` (8 factores), `RankingResult`, `ToolRanker`.
  Score normalizado entre 0.0 y 1.0.
- **`selection.py`** — `ToolSelectionEngine` con métodos:
  `select_tool()`, `select_tools()`, `rank_tools()`, `score_tool()`.
  Incluye `EngineConfig` para configuración completa.

#### Tests (133 nuevos)
- `tests/unit/test_tool_selection_exceptions.py` — 23 tests
- `tests/unit/test_tool_selection_filters.py` — 44 tests
- `tests/unit/test_tool_selection_ranking.py` — 40 tests
- `tests/unit/test_tool_selection.py` — 26 tests

#### Calidad
- ruff: ✅ All checks passed (nuevos archivos)
- black: ✅ Formatted
- mypy --strict: ✅ 0 errores en archivos nuevos
- pytest: ✅ 133/133 passed + 57 tests preexistentes sin regresión

#### Notas
- Sin cambios en la arquitectura existente.
- Compatibilidad total con el código preexistente.
- `__init__.py` actualizado a v0.3.0 con todas las nuevas exportaciones.

## [0.9.0] — 2026-07-16 — Sprint 3.10

### Sprint 3.10 — Finalización, Auditoría y Release

#### Resumen Técnico
Auditoría completa del Sprint 3 (módulos 3.1–3.9). Sin nuevas funcionalidades.
Todas las correcciones son incrementales y mantienen compatibilidad total.

#### Módulos Auditados (Sprint 3)
- `packages/core/src/planner.py` — Planner heurístico (Sprint 3.1)
- `packages/core/src/plan_models.py` — Modelos de estado del plan (Sprint 3.6)
- `packages/core/src/plan_executor.py` — Ejecutor de planes (Sprint 3.6)
- `packages/core/src/plan_tracking.py` — Seguimiento de ejecución (Sprint 3.7)
- `packages/core/src/pipeline.py` — Pipeline unificado (Sprint 3.6 + 3.9)
- `packages/core/src/execution_graph/` — Grafo de ejecución paralela (Sprint 3.8)
  - `models.py`, `dependency_graph.py`, `topological_sort.py`, `scheduler.py`,
    `ready_queue.py`, `parallel_executor.py`, `state_machine.py`, `retry_policy.py`, `recovery.py`
- `packages/core/src/context_engine/` — Context Engineering (Sprint 3.9)
  - `models.py`, `context_engine.py`, `context_builder.py`, `context_cache.py`,
    `context_budget.py`, `context_ranker.py`, `file_selector.py`, `memory_selector.py`,
    `history_selector.py`, `task_decomposer.py`, `subtask_generator.py`

#### Bugs Corregidos
- **`dependency_graph.remove_node`**: bug use-after-free — accedía a `self.edges[name]` después de `pop()`. Ahora guarda las dependencias antes de eliminar.
- **`pipeline.stage_plan_execution`**: el resultado del plan era sobrescrito por las etapas posteriores de selección y ejecución de agente. Ahora las etapas se saltan cuando el plan ya produjo respuesta.
- **`execution_graph/recovery.py`**: variable `nd` indefinida en `save_checkpoint` (error en tiempo de ejecución).
- **`execution_graph/recovery.py`**: `partial_replan` usaba `asyncio.get_event_loop().run_until_complete()` deprecado. Reemplazado por `asyncio.get_running_loop()` / `asyncio.run()`.
- **`execution_graph/recovery.py`**: `_retry_node` no limpiaba `completed_at` al resetear un nodo fallido.
- **`execution_graph/recovery.py`**: docstring de `partial_replan` con indentación incorrecta.
- **`context_engine/memory_selector.py` y `history_selector.py`**: `_parse_timestamp` llamaba a `fromisoformat()` 4 veces en un bucle inútil.

#### Optimización
- Eliminado código muerto: `_matches_any()` en `planner.py`, `levels` en `critical_path()`, `old` en `plan_tracking.resume_plan()`.
- Eliminados 12+ imports no utilizados en `dependency_graph.py`, `scheduler.py`, `retry_policy.py`, `ready_queue.py`, `recovery.py`, `plan_executor.py`, `plan_tracking.py`, `context_engine.py`.
- Movidos 5+ imports inline (`import time`, `import math`) al nivel superior del módulo en `file_selector.py`, `memory_selector.py`.
- `topological_sort.topological_levels()` ahora delega a `DependencyGraph.topological_levels()`, eliminando duplicación de lógica.
- Simplificada lógica confusa en `DependencyGraph.is_ready()`.
- Eliminado `_dedup_cache` no utilizado en `ContextCompressor`.
- Eliminado bloque `if TYPE_CHECKING: pass` vacío en `context_engine.py` y `scheduler.py`.

#### Tipado
- Todos los módulos Sprint 3 pasan ruff sin advertencias.
- Imports TYPE_CHECKING limpiados y verificados.

#### Documentación Nueva
- `docs/planner.md` — Documentación completa del sistema de planificación.
- `docs/context_engine.md` — Documentación del Context Engineering Engine.
- `docs/execution_graph.md` — Documentación del Execution Graph.
- `docs/planning_pipeline.md` — Documentación de integración del pipeline completo.

#### Exportaciones Públicas
- `packages/core/src/__init__.py` ahora exporta todos los módulos Sprint 3:
  `plan_models`, `plan_executor`, `plan_tracking`, y 38 nuevos eventos
  (PLAN_*, TRACKING_*, EXEC_GRAPH_*, CONTEXT_*).

#### Pruebas
- 805 tests passed (incluyendo 486 tests Sprint 3).
- Cobertura existente: execution_graph (1754 líneas), context_engine (1490 líneas),
  plan_executor (665 líneas), plan_tracking (1126 líneas).
- Ruff: 0 errores en todos los módulos Sprint 3.

#### Problemas Pendientes
- `plan_tracking.py`: 7 usos de `asyncio.get_event_loop()` (deprecado) en handlers de eventos — no corregido para no alterar comportamiento async existente.
- `parallel_executor.py`: `default_timeout` en config existe pero no se aplica wrapping con `asyncio.wait_for()` — marcado como enhancement futuro.
- `plan_executor.py`: `enable_parallel` y `max_parallel_tasks` en config son configuración muerta — la ejecución paralela via Execution Graph es la vía preferida.
- `plan_tracking.py`: algunos event handlers establecen estado directamente sin validar la máquina de estados — decisión intencional para eventos del sistema.
- `context_budget.py`: deduplicación usa `hash()` que no es determinista entre procesos — aceptable para cache en-memoria.

## [0.8.0] — 2026-07-16 — Sprint 3.7

### Sprint 3.7 — Plan Execution Tracking

#### Added
- **`PlanExecutionTracker`** (`packages/core/src/plan_tracking.py`):
  capa avanzada de seguimiento de ejecución de planes que permite conocer
  el progreso real de una tarea, detectar bloqueos y mantener
  sincronización entre Planner, Orchestrator y Multi-Agent System.
  - Estados de seguimiento: PENDING, PLANNING, EXECUTING, RUNNING_STEP,
    COMPLETED, FAILED, PAUSED, CANCELLED.
  - `TrackingState` con máquina de estados validada.
  - `StepSnapshot`: captura punto-a-punto del estado de un paso.
  - `PlanSnapshot`: captura punto-a-punto del estado completo del plan.
  - `BlockageReport`: reporte estructurado de bloqueos detectados.
  - Detección automática de bloqueos con timeout configurable.
  - Snapshots periódicos con persistencia vía `ExecutionRepository`.
  - Historial acotado de planes completados.
  - Métricas por plan incluyendo utilización de agentes.
- **8 nuevos eventos** en `events.py`: `tracking.plan.pending`,
  `tracking.plan.planning`, `tracking.plan.executing`,
  `tracking.plan.running_step`, `tracking.plan.blocked`,
  `tracking.plan.resumed`, `tracking.step.progress`,
  `tracking.snapshot.saved`.
- **`Orchestrator.attach_plan_tracker()`**: método para conectar el
  tracker al orquestador.
- **`PlanExecutor.attach_tracker()`**: método para conectar el tracker
  al executor, con actualizaciones automáticas de pasos.
- **83 tests** en `tests/unit/test_plan_tracking.py` cubriendo: creación de
  trackers, transiciones de estado, actualizaciones de pasos,
  detección de bloqueos, pause/resume/cancel, persistencia,
  tracking basado en eventos, integración con PlanExecutor, snapshots
  e historial.

#### Changed
- `packages/core/src/orchestrator.py`: añadida propiedad `plan_tracker`
  y método `attach_plan_tracker()`.
- `packages/core/src/plan_executor.py`: añadido método `attach_tracker()`
  y hooks de tracking en `_execute_single_task()` (assigned, running,
  completed, failed).

#### Architecture Notes
- Sin dependencias circulares: `plan_tracking` importa solo de
  `src.events` y `src.plan_models`.
- Compatibilidad total con sprints anteriores (Sprint 3.6 y anteriores).
- Baseline de tests: 944 passed (83 nuevos), 23 pre-existing failures
  (permisos pydantic, no relacionados).

## [0.7.0] — 2026-07-14 — Sprint 2.3 estable

### Sprint 2.3 — Agent Lifecycle & Registry Integration

#### Added
- **`AgentManifest`** (`registry/manifest.py`): contrato declarativo inmutable
  con validaciones (ID lowercase, version semver, no caps duplicadas,
  priority >= 0). `standard_manifests()` devuelve 7 manifests para los
  agentes de Sprint 2.1.
- **`LifecycleManager`** (`registry/lifecycle.py`): wrapper no intrusivo sobre
  `BaseAgent` con estados CREATED → INITIALIZING → READY ⇄ RUNNING → STOPPED,
  ERROR recuperable. Métodos `initialize()`, `start()`, `stop()`,
  `mark_error()`, `recover()`, `health_check()` (devuelve `LifecycleHealth`).
- **`RegistryOrchestrator`** (`registry/orchestrator.py`): orchestrator
  desacoplado que no conoce clases concretas. `bootstrap()` registra 7
  agentes estándar con manifests. `request(capability, task)` resuelve vía
  `CapabilityResolver`, ejecuta con lifecycle, registra usage.
- **API simplificada del `AgentRegistry`**: `register_simple`,
  `register_with_manifest`, `list_agents`, `list_manifests`,
  `find_by_capability_simple`, `find_manifests_by_capability`.
- **Documentación**: `docs/sprint-2.3-agent-lifecycle-report.md` con
  diagramas Mermaid (arquitectura, flujo request→agent, lifecycle).
- **Tests**: 100 nuevos en `tests/agents/` (test_manifest, test_lifecycle,
  test_registry, test_discovery).

#### Fixed
- `AgentOrchestrator.register_agent` ahora usa duck-typing (hasattr) en
  lugar de `isinstance` para ser robusto frente a re-imports del módulo.
- `AgentRegistry.list_manifests` usa duck-typing para validar manifests
  devueltos por `agent.manifest()`.
- `RegistryOrchestrator.request` sincroniza el lifecycle antes de `start()`
  para que `started_count` incremente correctamente en ejecuciones múltiples.

### Sprint 2.3.1 — Stabilization & Release Preparation

#### Added
- **Auditoría completa**: `docs/sprint-2.3.1-audit.md` con análisis de
  imports, dependencias circulares, duplicación, nombres, APIs públicas.
- **Tests de compatibilidad**: `tests/agents/test_api_compatibility.py`
  con 23 contract tests que verifican APIs clave (AgentRegistry, Lifecycle,
  RegistryOrchestrator, AgentManifest, BaseAgent, WorkflowEngine).
- **Performance baseline**: `docs/performance-baseline.md` con 9 métricas
  de tiempo y 2 de memoria. `scripts/perf_baseline.py` reproducible.

#### Fixed
- mypy: `_PATTERNS` en `planner.py` tipado incorrectamente (3 errores).
- mypy: `AgentOrchestrator.send_to` no aceptaba `correlation_id`. Añadido
  parámetro opcional.
- mypy: `# type: ignore[assignment]` en `api.py` no cubría código `misc`.
- `__init__.py` raíz ahora exporta `RegistryOrchestrator`, `AgentManifest`,
  `LifecycleManager`, `LifecycleState`, `standard_manifests`, etc.

#### Quality
- ruff check: All checks passed!
- mypy: Success, no issues found in 39 source files
- pytest: 457 tests passing (434 + 23 compat tests)
- Performance: discovery <0.01ms, request <1ms, bootstrap <1ms

### Compatibilidad
- Sprint 2.1+2.2: 334 tests siguen pasando.
- Sprint 1: 56+ tests verificados siguen pasando.
- `AgentOrchestrator`, `WorkflowOrchestrator` y `WorkflowEngine` coexisten
  sin cambios.

## [Unreleased] — Sprint 2.2/2 — Agent Registry + Capabilities Refinement

### Added
- **Registry subpackage** (`packages/multiagent/src/multiagent/registry/`):
  - `Capability` (inmutable, jerárquica con dots, wildcards `*`).
  - `CapabilityRegistry`: register/unregister/get/exists/all/by_category/
    search/categories/metrics. `register_standard_capabilities()` registra
    34 capabilities en 10 categorías.
  - `AgentRecord`: wrapper con id/name/version/capabilities/priority/status/
    metadata/created_at/last_seen/last_heartbeat/health/errors/usage.
  - `AgentRegistry`: register/unregister/replace/discover/get/get_all/
    exists/health/metrics. Auto-discovery via hooks + `_infer_capabilities`.
    Health con heartbeat loop, update_health, timeout.
  - `CapabilityResolver`: resolve(capability) → ResolutionResult con scoring
    ponderado (specialty + priority + cost + history + load + latency).
    `ResolverWeights` normalizables.
  - `BackwardCompatibilityAdapter`: traduce nombres legacy
    (PlannerAgent/CoderAgent/etc.) y actions (refactor/review/etc.) a
    capabilities. `auto_register_legacy_agents()`.
- **`RegistryAwareScheduler`** (`workflow/registry_scheduler.py`): scheduler
  que usa el `CapabilityResolver` en lugar del LoadBalancer directo. Hereda
  de `Scheduler`. `mark_step_finished()` registra usage en el AgentRecord.
  Fallback al LoadBalancer si el resolver miss.
- **Eventos nuevos**: `agent.registered`, `agent.removed`,
  `agent.health.changed`, `capability.resolved`, `capability.miss`,
  `scheduler.selected_agent`.
- **Documentación**: `docs/sprint-2.2-2-agent-registry-report.md` y
  `docs/sprint-2.2-2-diagrams.md` (8 diagramas Mermaid).
- **Tests**: 86 nuevos (Capability, CapabilityRegistry, AgentRegistry,
  CapabilityResolver, BackwardCompatibilityAdapter, RegistryAwareScheduler).

### Fixed
- `AgentRegistry.register` ahora usa duck-typing (hasattr) en lugar de
  `isinstance(agent, BaseAgent)` para ser robusto frente a re-imports del
  módulo durante tests.

### Compatibilidad
- Sprint 2.1+2.2: 248/248 tests siguen pasando.
- Sprint 1: 56+ tests verificados siguen pasando.
- Ningún workflow existente se rompe: el adapter traduce nombres y
  actions legacy a capabilities.

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