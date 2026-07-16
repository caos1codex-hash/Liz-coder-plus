# Pipeline de Planificación Completo — Sprint 3

## Visión General

Este documento describe el pipeline de planificación de extremo a extremo de
Liz Coder Plus: desde que un usuario envía un mensaje hasta que los agentes
ejecutan las subtareas y se devuelve la respuesta compuesta. Es el documento
de integración que muestra cómo todas las piezas del Sprint 3 se conectan.

El pipeline está implementado como una secuencia de 11 etapas (`StageFn`) en
`src/pipeline.py`, ejecutadas por `ExecutionPipeline` y orquestadas por
`Orchestrator`. Cada etapa es una función async que recibe `(Orchestrator, PipelineRequest)`
y retorna el `PipelineRequest` mutado.

## Flujo del Pipeline

### Etapa 1: Validación de Request

**Archivo:** `src/pipeline.py` — `stage_validate_request`

Valida la envolvente de la solicitud entrante:

- `message`: debe ser un string no vacío.
- `session_id`: debe ser un string no vacío.
- `mode`: debe ser `"confirmation"` o `"automatic"` (por defecto `"confirmation"`).

En caso de fallo, lanza `PipelineError` con `error_type="validation"`.

### Etapa 2: Publicación de Mensaje

**Archivo:** `src/pipeline.py` — `stage_publish_user_message`

Emite el evento `user.message.received` al `EventBus` con:
- `session_id`, `message_length`, `mode`.

Permite que los suscriptores de observabilidad reaccionen a cada mensaje entrante.

### Etapa 3: Persistencia

**Archivo:** `src/pipeline.py` — `stage_persist_user_turn`

Persiste el turno del usuario en el `SessionManager`:

1. Establece el modo de la sesión: `session_manager.set_mode(session_id, mode)`.
2. Agrega el turno: `session_manager.append_turn(session_id, role="user", content=message)`.

El SessionManager mantiene historial en RAM y lo sincroniza con SQLite a través
del `MemoryManager` (si está inicializado).

### Etapa 4: Construcción de Contexto

**Archivo:** `src/pipeline.py` — `stage_build_context`

Construye el contexto del agente desde el estado de la sesión:

1. **Contexto base:** `orchestrator._build_context(session_id)` retorna historial,
   idioma, modo y otros datos de sesión.
2. **ContextEngine (Sprint 3.9):** si el Orchestrator tiene un `_context_engine`
   adjunto, se enriquece el contexto con:
   ```python
   agent_ctx = await ctx_engine.build_context(
       task=message,
       agent_name=agent_name,
       session_id=session_id,
       history=context.get("history", []),
   )
   ```
   El resultado se almacena en `req.context["agent_context"]`.

Si el ContextEngine falla, se registra el error y se continúa con el contexto
estándar (degradación graciosa).

### Etapa 5: Recuperación de Memoria

**Archivo:** `src/pipeline.py` — `stage_retrieve_memory`

Si el `MemoryManager` está inicializado, recupera el contexto persistente
de la sesión:

```python
history = await memory.get_context(session_id)
req.context["persistent_history"] = history
```

Este historial persistente complementa el historial en RAM del SessionManager.

### Etapa 6: Planificación

**Archivo:** `src/pipeline.py` — `stage_plan_execution`

Si el Planner está adjunto al Orchestrator:

1. **Generar plan:** `planner.plan(message, context)` → lista de dicts de subtareas.
2. **Almacenar en metadata:** `req.metadata["plan"] = plan_result`.
3. **Ejecución completa (Sprint 3.6):** si el PlanExecutor está adjunto Y el
   plan tiene más de 1 subtarea:
   ```python
   executed_plan = await plan_executor.execute_plan(
       plan_result,
       original_message=message,
       context=context,
   )
   ```
4. **Construir respuesta compuesta:** se concatenan los resultados de las tareas
   completadas, se marca como omitidas las saltadas, y se reportan errores.
5. **Cortocircuito:** se establece `plan_execution_completed=True`, lo que
   provoca que las etapas 7-9 se omitan.

### Etapa 7: Ejecución del Plan

**Archivo:** `src/plan_executor.py` — `PlanExecutor.execute_plan`

Cuando el PlanExecutor ejecuta un plan:

1. **Crear ExecutablePlan** desde los dicts del Planner:
   - Asignar UUID, timestamps, estados iniciales.
   - Transicionar: `CREATED → VALIDATING → READY → RUNNING`.
2. **Validar dependencias:**
   - Verificar que todas las dependencias referencian tareas existentes.
   - Detectar ciclos mediante DFS tres colores.
   - Si hay errores → `FAILED`, emitir evento.
3. **Ejecutar tareas secuencialmente** (o en paralelo si `enable_parallel=True`):
   - Para cada tarea lista (`ready_tasks()`):
     - Asignar agente: usar el sugerido por el Planner o el agente por defecto.
     - Ejecutar: `agent.handle(description, context)` con timeout.
     - Éxito → `COMPLETED`, emitir `plan.task.completed`.
     - Fallo → reintentar con backoff exponencial o marcar `FAILED`.
     - Emitir `plan.task.started`, `plan.task.assigned`.
   - Pausa opcional entre tareas.
4. **Finalizar plan:**
   - Todas completadas → `COMPLETED`, emitir `plan.completed`.
   - Algún fallo → `FAILED`, emitir `plan.failed`.
5. **Persistir:** si hay `ExecutionRepository` adjunto, guardar snapshot.
6. **Tracking (Sprint 3.7):** si hay `PlanExecutionTracker` adjunto, actualizar
   progreso de cada paso en tiempo real.

**Alternativa paralela (Sprint 3.8):** si el plan tiene muchas tareas
independientes, se puede usar el `ParallelExecutor` del Execution Graph para
ejecutar en paralelo respetando el DAG de dependencias.

### Etapa 8: Selección de Agente

**Archivo:** `src/pipeline.py` — `stage_select_agent`

Si la ejecución del plan ya produjo respuesta (`plan_execution_completed=True`),
esta etapa se omite.

De lo contrario, se usa el `AgentRouter` para seleccionar el agente:

```python
agent = await orch.router.route(message, context)
req.agent = agent
req.agent_name = agent.name
```

Se emite `agent.invoked` con `agent_name`, `session_id`, `message_length`.

### Etapa 9: Ejecución del Agente

**Archivo:** `src/pipeline.py` — `stage_execute_agent`

Si `plan_execution_completed=True`, se omite.

Ejecuta el agente seleccionado con timeout configurable:

```python
response_text = await asyncio.wait_for(
    agent.handle(message, context),
    timeout=orchestrator.agent_timeout,  # 30s por defecto
)
```

Manejo de errores:
- `TimeoutError` → `PipelineError(stage="stage_execute_agent", error_type="timeout")`.
- `ValueError`/`TypeError` → `PipelineError(error_type="validation")`.
- Otras excepciones → `PipelineError(error_type="unexpected")`.

### Etapa 10: Publicación de Resultado del Agente

**Archivo:** `src/pipeline.py` — `stage_publish_agent_result`

Emite `agent.completed` con `agent_name`, `session_id`, `duration_ms`,
`response_length`.

### Etapa 11: Actualización de Memoria

**Archivo:** `src/pipeline.py` — `stage_update_memory`

Persiste el turno del asistente:

```python
await session_manager.append_turn(
    session_id, role="assistant",
    content=response,
    metadata={"agent": agent_name},
)
```

## Integración de Componentes

### Diagrama de Integración

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Orchestrator                                │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │ EventBus │  │ SessionMgr   │  │ ExecutionPipeline             │  │
│  │          │  │              │  │                               │  │
│  │ planner. │  │ RAM history  │  │ [1] validate                  │  │
│  │ plan.*   │  │ SQLite sync  │  │ [2] publish message           │  │
│  │ plan.task│  │              │  │ [3] persist turn              │  │
│  │ track.*  │  └──────┬───────┘  │ [4] build context ─────────┐  │  │
│  │ agent.*  │         │          │ [5] retrieve memory          │  │  │
│  │ tool.*   │         │          │ [6] plan execution ──────┐  │  │  │
│  │ metric.* │         │          │ [7] select agent          │  │  │  │
│  └────┬─────┘         │          │ [8] check permissions     │  │  │  │
│       │               │          │ [9] execute agent          │  │  │  │
│       │               │          │ [10] publish result        │  │  │  │
│       │               │          │ [11] update memory        │  │  │  │
│       │               │          └──────────────────────────┘  │  │  │
│       │               │                                      │  │  │
└───────┼───────────────┼──────────────────────────────────────┼──┘  │
        │               │                                      │     │
        ▼               ▼                                      ▼     │
┌───────────────┐ ┌───────────┐  ┌───────────────┐  ┌──────────────┐ │
│   Planner     │ │  Memory   │  │ PlanExecutor  │  │ AgentRouter  │ │
│               │ │  Manager  │  │               │  │              │ │
│ - Regex rules │ │ - SQLite  │  │ - ExecPlan    │  │ - route()    │ │
│ - SubTask     │ │ - Convos  │  │ - validate    │  │ - agents     │ │
│ - Plan        │ │ - Context │  │ - execute     │  │              │ │
│ - events      │ │           │  │ - retry       │  └──────┬───────┘ │
└───────┬───────┘ └───────────┘  └───────┬───────┘         │         │
        │                               │                  │         │
        ▼                               ▼                  ▼         │
┌───────────────┐  ┌──────────────────────────┐  ┌──────────────┐ │
│ ContextEngine │  │ PlanExecutionTracker    │  │ AgentRegistry│ │
│               │  │                          │  │              │ │
│ - build_ctx   │  │ - create_tracker         │  │ - register() │ │
│ - decompose   │  │ - update_step            │  │ - get()      │ │
│ - cache       │  │ - check_blockages        │  │ - names()    │ │
│ - FileSelector│  │ - save_snapshot          │  └──────────────┘ │
│ - MemSelector │  │ - cancel                 │                    │
│ - HistSelector│  │                          │  ┌──────────────┐ │
│ - Ranker      │  └──────────────────────────┘  │ ToolRegistry │ │
│ - Budget      │                                │              │ │
│ - Compressor  │  ┌──────────────────────────┐  │ - register() │ │
└───────────────┘  │ Execution Graph          │  │ - get()      │ │
                   │                          │  │ - execute()  │ │
                   │ - DependencyGraph        │  │ - enabled    │ │
                   │ - ParallelExecutor       │  └──────────────┘ │
                   │ - RetryPolicy            │                    │
                   │ - RecoveryManager        │  ┌──────────────┐ │
                   │ - ExecutionStateMachine  │  │   Workflow    │ │
                   │ - ReadyQueue             │  │              │ │
                   │                          │  │ - create()   │ │
                   └──────────────────────────┘  │ - execute()  │ │
                                                 └──────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Cómo se conectan los componentes

1. **Planner → PlanExecutor**: el Planner produce `list[dict]`, el PlanExecutor
   los consume para crear un `ExecutablePlan`.
2. **PlanExecutor → AgentRouter**: cada tarea del plan se ejecuta a través de
   un agente obtenido del AgentRouter.
3. **PlanExecutor → PlanExecutionTracker**: el tracker se adjunta vía
   `attach_tracker()` y recibe actualizaciones automáticas.
4. **ContextEngine → Pipeline**: inyectado en el Orchestrator, se invoca en
   `stage_build_context` para enriquecer el contexto.
5. **Execution Graph → PlanExecutor**: cuando `enable_parallel=True`, el
   PlanExecutor delega al `ParallelExecutor` del Execution Graph.
6. **MemoryManager → Pipeline**: proporciona historial persistente en
   `stage_retrieve_memory` y persiste turnos en `stage_persist_user_turn`.
7. **EventBus → Todos**: todos los componentes emiten eventos al bus central
   para observabilidad, logging y auditoría.
8. **ToolRegistry → Agentes**: los agentes usan herramientas del registry
   a través del `ToolExecutor` (con permisos).

## Modelos Compartidos

### Flujo de datos entre componentes

```
Usuario: "Agrega autenticación con Google OAuth"
  │
  ▼ [PipelineRequest]
message="Agrega autenticación con Google OAuth"
session_id="abc123"
mode="confirmation"
  │
  ▼ [stage_build_context]
context = {
    "history": [...],
    "language": "es",
    "mode": "confirmation",
    "agent_context": AgentContext(        ← ContextEngine
        task="Agrega autenticación...",
        files=[ContextFile("src/auth/config.py", score=0.92)],
        memories=[ContextMemory("oauth_patterns", score=0.85)],
        history=[ContextHistory("user", "fix login...", score=0.78)],
        system_prompt="...",
        total_tokens=12450,
    )
}
  │
  ▼ [stage_plan_execution]
metadata["plan"] = [
    {"name": "investigate_auth", "suggested_agent": "research", ...},
    {"name": "create_auth_config", "depends_on": ["investigate_auth"], ...},
    {"name": "implement_auth_backend", "depends_on": ["create_auth_config"], ...},
    {"name": "implement_auth_frontend", "depends_on": ["implement_auth_backend"], ...},
    {"name": "add_auth_tests", "depends_on": ["implement_auth_backend", ...], ...},
    {"name": "update_auth_docs", "depends_on": ["add_auth_tests"], ...},
]
  │
  ▼ [PlanExecutor.execute_plan]
metadata["executed_plan"] = ExecutablePlan(
    id="uuid-...",
    state=PlanState.COMPLETED,
    tasks=[
        PlanTask(name="investigate_auth", status=COMPLETED, result="..."),
        PlanTask(name="create_auth_config", status=COMPLETED, result="..."),
        PlanTask(name="implement_auth_backend", status=COMPLETED, result="..."),
        PlanTask(name="implement_auth_frontend", status=COMPLETED, result="..."),
        PlanTask(name="add_auth_tests", status=COMPLETED, result="..."),
        PlanTask(name="update_auth_docs", status=SKIPPED, error=None),
    ],
    metrics={progress=1.0, total_duration_ms=45230, total_retries=0}
)
  │
  ▼ [stage_publish_agent_result]
response = "Investigación completada...\n\nConfiguración creada...\n\n..."
metadata["plan_execution_completed"] = True
metadata["plan_metrics"] = {progress: 1.0, state: "completed", ...}
```

### Tipos de datos clave

| Tipo | Definido en | Usado por |
|------|-------------|-----------|
| `PipelineRequest` | `pipeline.py` | Todas las etapas |
| `Plan` | `planner.py` | Planner |
| `SubTask` | `planner.py` | Planner → PlanExecutor |
| `ExecutablePlan` | `plan_models.py` | PlanExecutor, Tracker |
| `PlanTask` | `plan_models.py` | PlanExecutor |
| `PlanState` | `plan_models.py` | ExecutablePlan |
| `PlanTaskStatus` | `plan_models.py` | PlanTask |
| `TrackingState` | `plan_tracking.py` | PlanExecutionTracker |
| `StepSnapshot` | `plan_tracking.py` | Tracker → UI |
| `PlanSnapshot` | `plan_tracking.py` | Tracker → Persistence |
| `AgentContext` | `context_engine/models.py` | ContextEngine → Agent |
| `TaskBreakdown` | `context_engine/models.py` | TaskDecomposer |
| `SubTaskMeta` | `subtask_generator.py` | SubTaskGenerator |
| `DependencyGraph` | `dependency_graph.py` | Scheduler, Executor |
| `ExecutionNode` | `execution_graph/models.py` | ParallelExecutor |
| `ExecutionNodeState` | `execution_graph/models.py` | StateMachine |
| `ExecutionResult` | `execution_graph/models.py` | ParallelExecutor |
| `ScheduleDecision` | `scheduler.py` | Scheduler → Executor |

## Ejemplo Completo

### Solicitud del usuario

> "Busca información sobre el rendimiento de la API y luego optimiza las consultas a la base de datos"

### Paso a paso

**[1] Validación** — `stage_validate_request`
- message no vacío ✓
- session_id no vacío ✓
- mode="confirmation" ✓

**[2] Publicación** — `stage_publish_user_message`
- Evento: `user.message.received` {session_id, message_length=89, mode="confirmation"}

**[3] Persistencia** — `stage_persist_user_turn`
- SessionManager: `set_mode("abc123", "confirmation")`
- SessionManager: `append_turn("abc123", role="user", content="Busca información...")`

**[4] Construcción de Contexto** — `stage_build_context`
- Contexto base: `{history: [...], language: "es"}`
- ContextEngine invocado:
  - FileSelector: selecciona `src/db/repository.py` (score=0.88), `src/api/routes.py`
    (score=0.75), `tests/test_api.py` (score=0.65)
  - MemorySelector: selecciona `{"key": "slow_queries", value: "..."}` (score=0.82)
  - HistorySelector: selecciona turnos previos sobre "API" y "rendimiento"
  - ContextRanker: reordena con diversity boost
  - BudgetManager: aplica presupuesto 40% archivos, 30% memoria, 30% historial
  - Resultado: `AgentContext` con 3 archivos, 1 memoria, 2 turnos, ~12K tokens

**[5] Recuperación de Memoria** — `stage_retrieve_memory`
- MemoryManager: `get_context("abc123")` → historial persistente de sesiones anteriores

**[6] Planificación** — `stage_plan_execution`
- Planner.plan(): patrón "research" coincide (`busca...y luego`)
- Plan generado:
  ```
  estrategia: "research_then_summarize"
  subtareas: [
    {name: "gather", agent: default, tools: [file_tool, system_tool]},
    {name: "analyze", depends_on: ["gather"]},
    {name: "summarize", depends_on: ["analyze"]},
  ]
  ```
- len(subtareas) > 1 → PlanExecutor.execute_plan()
- ExecutablePlan creado:
  ```
  state: CREATED → VALIDATING → READY → RUNNING
  tasks: [gather(PENDING), analyze(PENDING), summarize(PENDING)]
  ```
- Validación de dependencias: sin ciclos ✓
- Ejecución secuencial:
  - `gather`: agente asignado → ejecuta → resultado="Encontradas 3 consultas lentas..." → COMPLETED
  - `analyze`: agente asignado → ejecuta → resultado="Las consultas N+1 son el problema..." → COMPLETED
  - `summarize`: agente asignado → ejecuta → resultado="Resumen: optimizar consultas N+1 en repository.py..." → COMPLETED
- Plan → COMPLETED
- Respuesta compuesta almacenada en `req.response`

**[7] Selección de Agente** — `stage_select_agent`
- OMITIDO (`plan_execution_completed=True`)

**[8] Permisos** — `stage_check_permissions`
- No-op (diferido a Sprint futuro)

**[9] Ejecución del Agente** — `stage_execute_agent`
- OMITIDO (`plan_execution_completed=True`)

**[10] Publicación** — `stage_publish_agent_result`
- Evento: `agent.completed` {duration_ms: 0, response_length: 450}

**[11] Actualización de Memoria** — `stage_update_memory`
- SessionManager: `append_turn("abc123", role="assistant", content="Encontradas 3 consultas...")`

### Resultado Final

```json
{
    "type": "message",
    "content": "Encontradas 3 consultas lentas en repository.py\n\nLas consultas N+1 son el problema principal...\n\nResumen: optimizar consultas N+1 en repository.py añadiendo joinedload...",
    "status": "completed",
    "session_id": "abc123",
    "agent_name": "",
    "duration_ms": 45230,
    "stage_timings": {
        "stage_validate_request": 0,
        "stage_publish_user_message": 1,
        "stage_persist_user_turn": 5,
        "stage_build_context": 120,
        "stage_retrieve_memory": 8,
        "stage_plan_execution": 45000,
        "stage_select_agent": 0,
        "stage_check_permissions": 0,
        "stage_execute_agent": 0,
        "stage_publish_agent_result": 1,
        "stage_update_memory": 3
    }
}
```

### Eventos emitidos durante el pipeline

```
user.message.received
context.engine.cache_miss
context.engine.files_selected
context.engine.memories_selected
context.engine.history_selected
context.engine.built
memory.retrieved
planner.plan.created
planner.decision × 3
plan.created
plan.validating
plan.ready
plan.started
plan.task.assigned × 3
plan.task.started × 3
plan.task.completed × 3
plan.completed
tracking.plan.pending
tracking.plan.planning
tracking.plan.executing
tracking.plan.running_step × 3
tracking.step.progress × 3
tracking.snapshot.saved
agent.completed
```

### Métricas del plan

```json
{
    "plan_id": "a1b2c3d4",
    "strategy": "research_then_summarize",
    "state": "completed",
    "progress": 1.0,
    "task_count": 3,
    "completed": 3,
    "failed": 0,
    "skipped": 0,
    "total_duration_ms": 45230.5,
    "total_retries": 0
}
```