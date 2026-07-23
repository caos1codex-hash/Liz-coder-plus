# Sistema de Planificación — Sprint 3

## Visión General

El sistema de planificación de Liz Coder Plus es un pipeline multi-etapa que transforma
una solicitud de usuario en una secuencia ejecutable de subtareas coordinadas por
múltiples agentes. El pipeline opera de forma completamente heurística (sin dependencia
de LLM), lo que permite planificación determinista y rápida.

El flujo completo del pipeline es:

```
Solicitud del usuario
  │
  ▼
┌─────────────────────┐
│ 1. Detección de     │  ← Análisis del mensaje de entrada
│    Intención        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 2. Análisis de      │  ← Clasificación del tipo de tarea
│    Tarea            │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 3. Descomposición   │  ← División en subtareas (Planner / TaskDecomposer)
│    de Tareas        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 4. Grafo de         │  ← Construcción del DAG de ejecución
│    Ejecución        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 5. Resolución de    │  ← Validación de dependencias, detección de ciclos
│    Dependencias     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 6. Ingeniería de    │  ← ContextEngine: archivos, memoria, historial
│    Contexto         │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 7. Recuperación de  │  ← Memoria persistente (SQLite)
│    Memoria          │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 8. Seguimiento de   │  ← PlanExecutionTracker: estados, bloqueos, snapshots
│    Ejecución        │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 9. Salida del       │  ← Respuesta compuesta, métricas, eventos
│    Planificador     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ 10. Sistema         │  ← Despacho paralelo/secuencial a agentes
│     Multi-Agente    │
└─────────────────────┘
```

## Componentes Principales

### Planner (`src/planner.py`)

El `Planner` es el planificador heurístico original, introducido en el Sprint 1.7
y extendido significativamente en el Sprint 3. Utiliza patrones de expresiones
regulares precompiladas para clasificar la intención del usuario y producir un
`Plan` con subtareas estructuradas.

**Patrones de detección (precompilados):**

| Patrón | Regex clave | Estrategia generada |
|--------|-------------|---------------------|
| Archivos | `lee`, `escribe`, `lista` + `archivo` | `file_operation` |
| Terminal | `ejecuta`, `corre`, `run` + `comando` | `terminal_command` |
| Investigación | `busca`, `investiga` + `y luego` | `research_then_summarize` |
| Sistema | `información`, `memoria`, `cpu` + `sistema` | `system_info` |
| Fallback | — | `single` |

**Modelo de datos:**

- `SubTask`: subtarea individual con nombre, descripción, agente sugerido,
  herramientas sugeridas y lista de dependencias.
- `Plan`: contiene una estrategia (`strategy`), una lista de `SubTask`
  ordenadas y una justificación (`rationale`).

**API Pública:**

```python
planner = Planner(agent_registry=..., tool_registry=..., memory=..., event_bus=...)

# Generar plan como lista de dicts (compatible con WorkflowBuilder)
subtasks = await planner.plan(message, context)

# Generar plan con el objeto Plan completo
plan = await planner.plan_detailed(message, context)
```

**Reglas de planificación:**

1. `file_operation`: `read → process → respond` (3 subtareas)
2. `terminal_command`: `validate → execute → respond` (3 subtareas)
3. `research_then_summarize`: `gather → analyze → summarize` (3 subtareas)
4. `system_info`: `query → respond` (2 subtareas)
5. `single` (fallback): una sola subtarea `handle` con el agente por defecto

### Plan Models (`src/plan_models.py`)

Introducido en el Sprint 3.6, define las máquinas de estado y modelos de datos
para el flujo de ejecución del planificador.

**`PlanState` (8 estados):**

```
CREATED → VALIDATING → READY → RUNNING → COMPLETED
                                ↘→ FAILED
                      RUNNING → PAUSED → RUNNING
                      FAILED → VALIDATING  (re-plan)
```

| Estado | Descripción |
|--------|-------------|
| `CREATED` | Plan recién creado |
| `VALIDATING` | Validando dependencias |
| `READY` | Listo para ejecución |
| `RUNNING` | En ejecución |
| `PAUSED` | Pausado (puede resumir) |
| `COMPLETED` | Completado (terminal) |
| `FAILED` | Fallido (puede re-planificar) |
| `CANCELLED` | Cancelado (terminal) |

**`PlanTaskStatus` (6 estados):**

```
PENDING → ASSIGNED → RUNNING → COMPLETED
                              ↘→ FAILED → ASSIGNED (retry)
                              ↘→ SKIPPED
```

| Estado | Descripción |
|--------|-------------|
| `PENDING` | Esperando ejecución |
| `ASSIGNED` | Agente asignado |
| `RUNNING` | En ejecución |
| `COMPLETED` | Completado (terminal) |
| `FAILED` | Fallido (puede reintentar) |
| `SKIPPED` | Omitido (terminal) |

**`PlanTask`**: dataclass con UUID, nombre, descripción, agente asignado,
dependencias, estado, resultado, error, reintentos, timestamps y métricas.

**`ExecutablePlan`**: envuelve la salida del Planner con gestión de ciclo de vida
completo. Proporciona:
- `ready_tasks()`: retorna tareas cuyas dependencias están completadas
- `next_task()`: primera tarea lista
- `progress`: ratio 0.0..1.0 de avance
- `metrics()`: métricas agregadas (duración, reintentos, conteos)
- `to_dict()`: serialización JSON-compatible

### Plan Executor (`src/plan_executor.py`)

Introducido en el Sprint 3.6, es el puente entre la salida del Planner y la
ejecución real a través del Orchestrator.

**`PlanExecutorConfig`:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `default_agent_timeout` | 30.0s | Timeout por tarea |
| `max_retries` | 3 | Reintentos máximos por tarea |
| `retry_backoff_base` | 0.5s | Base para backoff exponencial |
| `retry_backoff_cap` | 30.0s | Cap del backoff |
| `continue_on_error` | True | Continuar ante errores no críticos |
| `enable_parallel` | False | Habilitar ejecución paralela |
| `max_parallel_tasks` | 4 | Máximo de tareas paralelas |

**Flujo de ejecución:**

1. Construir `ExecutablePlan` desde los dicts del Planner
2. Validar dependencias (incluyendo detección de ciclos DFS)
3. Transicionar: `CREATED → VALIDATING → READY → RUNNING`
4. Para cada tarea lista:
   - Asignar agente
   - Ejecutar vía Orchestrator agent
   - Éxito → `COMPLETED`
   - Fallo → reintentar o marcar `FAILED`
   - Emitir eventos de progreso
5. Emitir `plan.completed` o `plan.failed`
6. Integrar con `PlanExecutionTracker` (Sprint 3.7)

**Integraciones:**
- `ExecutionRepository`: persistencia de historial de planes
- `PlanExecutionTracker`: seguimiento avanzado en tiempo real
- `ResilienceHelper`: manejo de errores y resiliencia

### Plan Tracking (`src/plan_tracking.py`)

Introducido en el Sprint 3.7, proporciona una capa avanzada de seguimiento
entre el Planner, PlanExecutor, Orchestrator y el Sistema Multi-Agente.

**`TrackingState` (8 estados):**

```
PENDING → PLANNING → EXECUTING → RUNNING_STEP
                                → COMPLETED
                                → FAILED
                                → PAUSED → RUNNING_STEP / CANCELLED
                                → CANCELLED
```

**Modelos de snapshot:**

- `StepSnapshot`: captura puntual del estado de un paso individual
  (task_id, nombre, estado, agente, tiempo de ejecución, reintentos, error, resultado)
- `PlanSnapshot`: captura puntual del estado completo del plan
  (plan_id, estrategia, estado, progreso, pasos, métricas, timestamps)
- `BlockageReport`: descripción estructurada de un bloqueo detectado
  (tipo, tarea bloqueada, razón, dependencias faltantes, sugerencias)

**`PlanExecutionTracker`:**

- Gestión en tiempo real del progreso de cada plan
- Detección automática de bloqueos (tareas que no pueden progresar)
- Persistencia de snapshots vía `ExecutionRepository`
- Emisión de eventos `tracking.*` para UI, logging y auditoría
- APIs para crear, actualizar, consultar y cancelar seguimiento

## Pipeline de Ejecución

El pipeline unificado se define en `src/pipeline.py` con las siguientes etapas
en orden:

```
1. stage_validate_request     → Validar la solicitud entrante
2. stage_publish_user_message → Emitir evento user.message.received
3. stage_persist_user_turn    → Persistir turno en SessionManager (RAM + SQLite)
4. stage_build_context        → Construir contexto del agente (con ContextEngine si disponible)
5. stage_retrieve_memory      → Recuperar memoria persistente (history)
6. stage_plan_execution       → Invocar Planner + PlanExecutor si hay múltiples subtareas
7. stage_select_agent         → Seleccionar agente vía AgentRouter
8. stage_check_permissions    → Verificar permisos (diferido a Sprint futuro)
9. stage_execute_agent        → Ejecutar agente con timeout
10. stage_publish_agent_result → Emitir evento agent.completed
11. stage_update_memory       → Persistir turno del asistente
```

**Cortocircuito del plan**: cuando el PlanExecutor produce una respuesta
completa (en `stage_plan_execution`), las etapas 7-9 se saltan y la
respuesta compuesta se devuelve directamente.

## Eventos del Sistema

### Eventos del Planificador (`planner.*`)

| Evento | Sprint | Descripción |
|--------|--------|-------------|
| `planner.plan.created` | 1.7 | Plan creado con estrategia y conteo |
| `planner.decision` | 1.7 | Decisión por subtarea (agente, herramientas, deps) |

### Eventos del Plan (`plan.*`)

| Evento | Sprint | Descripción |
|--------|--------|-------------|
| `plan.created` | 3.6 | ExecutablePlan creado |
| `plan.validating` | 3.6 | Validando dependencias |
| `plan.ready` | 3.6 | Plan listo para ejecución |
| `plan.started` | 3.6 | Ejecución iniciada |
| `plan.paused` | 3.6 | Plan pausado |
| `plan.completed` | 3.6 | Plan completado exitosamente |
| `plan.failed` | 3.6 | Plan fallido |
| `plan.cancelled` | 3.6 | Plan cancelado |

### Eventos de Tarea del Plan (`plan.task.*`)

| Evento | Sprint | Descripción |
|--------|--------|-------------|
| `plan.task.assigned` | 3.6 | Tarea asignada a un agente |
| `plan.task.started` | 3.6 | Tarea iniciada |
| `plan.task.completed` | 3.6 | Tarea completada |
| `plan.task.failed` | 3.6 | Tarea fallida |
| `plan.task.skipped` | 3.6 | Tarea omitida |
| `plan.task.retry` | 3.6 | Tarea en reintento |

### Eventos de Seguimiento (`tracking.*`)

| Evento | Sprint | Descripción |
|--------|--------|-------------|
| `tracking.plan.pending` | 3.7 | Plan en estado pendiente |
| `tracking.plan.planning` | 3.7 | Plan en planificación |
| `tracking.plan.executing` | 3.7 | Plan en ejecución |
| `tracking.plan.running_step` | 3.7 | Paso individual en ejecución |
| `tracking.plan.blocked` | 3.7 | Plan bloqueado detectado |
| `tracking.plan.resumed` | 3.7 | Plan reanudado |
| `tracking.step.progress` | 3.7 | Progreso de un paso |
| `tracking.snapshot.saved` | 3.7 | Snapshot persistido |

## API Pública

### `Planner`

```python
class Planner:
    def __init__(self, *, agent_registry, tool_registry, memory, event_bus)
    async def plan(message: str, context: dict | None) -> list[dict]
    async def plan_detailed(message: str, context: dict | None) -> Plan
```

### `PlanExecutor`

```python
class PlanExecutor:
    def __init__(self, *, orchestrator, event_bus, config)
    def attach_tracker(tracker) -> None
    def attach_execution_repository(repo) -> None
    async def execute_plan(subtasks, *, original_message, strategy, rationale, context) -> ExecutablePlan
```

### `PlanExecutionTracker`

```python
class PlanExecutionTracker:
    def __init__(self, *, event_bus, execution_repo)
    def create_tracker(plan, session_id) -> str
    def update_step(tracker_id, step_index, status, **kwargs) -> None
    def get_tracker(tracker_id) -> dict | None
    async def save_snapshot(tracker_id) -> None
    def check_blockages(tracker_id) -> list[BlockageReport]
    def cancel_tracker(tracker_id) -> None
```

### `ExecutablePlan`

```python
class ExecutablePlan:
    def transition_to(new_state: PlanState) -> None
    def ready_tasks() -> list[PlanTask]
    def next_task() -> PlanTask | None
    def get_task(task_id: str) -> PlanTask | None
    def get_task_by_name(name: str) -> PlanTask | None
    def metrics() -> dict
    def to_dict() -> dict
```

## Flujo Completo

```
Usuario envía mensaje: "Investiga el rendimiento y luego optimiza la base de datos"
  │
  ▼
[1] PipelineRequest creado con message + session_id + mode
  │
  ▼
[2] stage_validate_request → mensaje no vacío, session_id válido, modo conocido
  │
  ▼
[3] stage_publish_user_message → evento user.message.received
  │
  ▼
[4] stage_persist_user_turn → SessionManager.append_turn(role="user")
  │
  ▼
[5] stage_build_context → ContextEngine.build_context() enriquece el contexto
  │  ├─ FileSelector: selecciona archivos relevantes
  │  ├─ MemorySelector: selecciona memorias relevantes
  │  ├─ HistorySelector: selecciona historial relevante
  │  ├─ ContextRanker: reordena por score compuesto
  │  ├─ ContextBudgetManager: aplica presupuesto de tokens
  │  └─ ContextCompressor: comprime si es necesario
  │
  ▼
[6] stage_retrieve_memory → MemoryManager.get_context()
  │
  ▼
[7] stage_plan_execution
  │  ├─ Planner.plan() → research_then_summarize (3 subtareas)
  │  ├─ Len(subtasks) > 1 → PlanExecutor.execute_plan()
  │  │  ├─ Crear ExecutablePlan (CREATED)
  │  │  ├─ Validar dependencias (VALIDATING → READY)
  │  │  ├─ Iniciar ejecución (RUNNING)
  │  │  │  ├─ Tarea "gather": asignar agente → ejecutar → COMPLETED
  │  │  │  ├─ Tarea "analyze": asignar agente → ejecutar → COMPLETED
  │  │  │  └─ Tarea "summarize": asignar agente → ejecutar → COMPLETED
  │  │  └─ Plan COMPLETED → emitir eventos
  │  └─ Respuesta compuesta almacenada en req.response
  │
  ▼
[8] stage_select_agent → OMITIDO (plan_execution_completed = True)
  │
  ▼
[9] stage_execute_agent → OMITIDO (respuesta ya disponible)
  │
  ▼
[10] stage_publish_agent_result → evento agent.completed
  │
  ▼
[11] stage_update_memory → SessionManager.append_turn(role="assistant")
  │
  ▼
Respuesta final al usuario con resultados de las 3 subtareas
```

## Origen por Sprint

| Componente | Sprint | Descripción |
|------------|--------|-------------|
| `Planner` | 1.7 | Planificador heurístico inicial |
| `SubTask`, `Plan` | 1.7 | Modelos de datos del plan |
| `PlanState`, `PlanTaskStatus` | 3.6 | Máquinas de estado formales |
| `ExecutablePlan`, `PlanTask` | 3.6 | Modelos ejecutables con ciclo de vida |
| `PlanExecutor` | 3.6 | Ejecutor con validación, reintentos, tracking |
| `TrackingState`, `StepSnapshot` | 3.7 | Seguimiento avanzado en tiempo real |
| `PlanExecutionTracker` | 3.7 | Servicio de tracking con snapshots |
| `ContextEngine` | 3.9 | Ingeniería de contexto inteligente |
| `ExecutionGraph` | 3.8 | DAG con ejecución paralela |
| `DependencyGraph` | 3.8 | Grafo DAG con detección de ciclos |
| `ParallelExecutor` | 3.8 | Ejecutor paralelo asyncio |