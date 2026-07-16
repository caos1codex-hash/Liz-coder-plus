# Execution Graph — Sprint 3.8

## Visión General

El Execution Graph es el subsistema que habilita la ejecución paralela de planes
basándose en un grafo dirigido acíclico (DAG). Mientras el `PlanExecutor` del Sprint 3.6
ejecuta tareas secuencialmente respetando dependencias, el Execution Graph del Sprint 3.8
introduce ejecución paralela real mediante `asyncio`, permitting que tareas
independientes se ejecuten simultáneamente.

**Beneficios clave:**

- **Velocidad**: tareas sin dependencias mutuas se ejecutan en paralelo, reduciendo
  el tiempo total de ejecución.
- **Control de concurrencia**: `max_parallel` limita la cantidad de tareas
  simultáneas para evitar saturación de recursos.
- **Recuperación robusta**: checkpointing, rollback y re-planificación parcial
  permiten recuperarse de fallos granulares.
- **Observabilidad completa**: máquina de estado centralizada con historial de
  transiciones y métricas detalladas.

**Arquitectura del paquete:**

```
execution_graph/
├── __init__.py
├── models.py              ← ExecutionNode, ExecutionNodeState, ExecutionResult
├── dependency_graph.py    ← DependencyGraph (DAG), DependencyCycleError
├── topological_sort.py    ← Algoritmo de ordenamiento de Kahn
├── scheduler.py           ← ParallelScheduler, ScheduleDecision
├── parallel_executor.py   ← ParallelExecutor (asyncio), ParallelExecutorConfig
├── retry_policy.py        ← RetryPolicy (backoff exponencial con jitter)
├── recovery.py            ← RecoveryManager, RecoveryCheckpoint
├── state_machine.py       ← ExecutionStateMachine, StateTransition
└── ready_queue.py         ← ReadyQueue (heap con prioridades)
```

## Componentes

### DependencyGraph (`dependency_graph.py`)

Construye y gestiona un DAG a partir de un `ExecutablePlan` o lista de dicts
de subtareas. Usa nombres de tarea como identificadores de nodos.

**Estructura de datos:**

```python
@dataclass
class DependencyGraph:
    nodes: set[str]                        # Todos los nodos
    edges: dict[str, set[str]]             # {nodo: conjunto de dependencias}
    dependents: dict[str, set[str]]        # {nodo: conjunto de dependientes}
    priorities: dict[str, int]             # {nodo: prioridad} (menor = mayor prioridad)
```

Las aristas apuntan del nodo a sus dependencias: una arista `A → B` significa
que A depende de B, por lo que B debe completarse antes de que A pueda iniciar.

**Operaciones de construcción:**

```python
graph = DependencyGraph()
graph.add_node("task_c", dependencies=["task_a", "task_b"], priority=1)
graph.add_dependency("task_d", "task_c")  # task_d depende de task_c
graph.remove_node("task_b")
```

**Consultas:**

```python
graph.parents("task_c")          # → {"task_a", "task_b"} (predecesores)
graph.children("task_a")         # → {"task_c"} (sucesores)
graph.roots()                    # → nodos sin dependencias (puntos de entrada)
graph.leaves()                   # → nodos que nadie depende (puntos de salida)
graph.is_ready("task_c", completed)  # → True si todas sus deps están completadas
graph.remaining_dependencies("task_c", completed)  # → deps no completadas
graph.in_degree("task_c")        # → número de dependencias
graph.out_degree("task_a")       # → número de dependientes
```

**Validación:**

```python
errors = graph.validate()  # Lista de mensajes de error
# Errores detectados:
# 1. Dependencias rotas (deps en nodos inexistentes)
# 2. Ciclos (vía DFS tres colores)
```

**Detección de ciclos:**

Utiliza el algoritmo DFS de tres colores (WHITE → GRAY → BLACK). Cuando se
encuentra un nodo GRAY durante el recorrido, se extrae el ciclo y se lanza
`DependencyCycleError` con la lista de nodos del ciclo.

**Ordenamiento topológico:**

```python
order = graph.topological_order()       # Algoritmo de Kahn con desempate por prioridad
levels = graph.topological_levels()     # {nodo: nivel} (0 = raíces)
```

**Camino crítico:**

```python
path = graph.critical_path(durations={"task_a": 100, "task_b": 200, ...})
# → camino más largo a través del DAG
```

Calcula el tiempo de finalización más temprano para cada nodo y traza
hacia atrás desde el nodo con mayor tiempo de finalización.

**Subgrafo afectado:**

```python
affected = graph.affected_subgraph("failed_node")
# → subgrafo con el nodo fallido + todos sus dependientes transitivos
```

### ExecutionNode (`models.py`)

Nodo individual del DAG de ejecución con su propia máquina de estado.

**`ExecutionNodeState` (9 estados):**

```
PENDING → READY → RUNNING → COMPLETED
                     |→ FAILED → RETRYING → READY → RUNNING
                     |→ WAITING → READY
                     |→ SKIPPED
                     |→ CANCELLED
```

| Estado | Descripción | Transiciones permitidas |
|--------|-------------|------------------------|
| `PENDING` | Inicial, esperando | → READY, SKIPPED, CANCELLED |
| `READY` | Dependencias satisfechas | → RUNNING, WAITING, SKIPPED, CANCELLED |
| `WAITING` | Esperando recurso externo | → READY, CANCELLED |
| `RUNNING` | En ejecución activa | → COMPLETED, FAILED, CANCELLED |
| `FAILED` | Fallido | → RETRYING, SKIPPED, CANCELLED |
| `RETRYING` | En cola para reintento | → READY, CANCELLED |
| `COMPLETED` | Completado (terminal) | — |
| `SKIPPED` | Omitido (terminal) | — |
| `CANCELLED` | Cancelado (terminal) | — |

**`ExecutionNode`** (dataclass):

Atributos principales: `id` (UUID4), `name`, `description`, `agent_assigned`,
`dependencies`, `state`, `result`, `error`, `retry_count`, `max_retries`,
`started_at`, `completed_at`, `execution_time_ms`, `priority`, `topological_level`.

**`ExecutionResult`**: resultado agregado de la ejecución del grafo completo:
`graph_id`, `status`, `nodes_total/completed/failed/skipped`, `duration_ms`,
`max_parallelism`, `retry_count`, `critical_path_ms`, `error`.

### ParallelScheduler (`scheduler.py`)

Analiza el DAG, detecta tareas independientes y produce lotes de ejecución
respetando `max_parallel`.

**`SchedulerConfig`:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `max_parallel` | 4 | Máximo de nodos ejecutándose simultáneamente |
| `enable_preemption` | False | Permitir que nodos de mayor prioridad interrumpan |

**`ScheduleDecision`:**

```python
@dataclass
class ScheduleDecision:
    batch: list[str]         # Nodos a ejecutar en paralelo
    level: int               # Nivel topológico del lote
    reason: str              # Razón del scheduling
    available_slots: int     # Slots restantes después del lote
```

**Algoritmo de scheduling:**

1. Escanear todos los nodos del grafo.
2. Filtrar: excluir running, completed, skipped, cancelled, failed (no en retrying).
3. Para cada nodo elegible, verificar si todas sus dependencias están completadas.
4. Los nodos listos se insertan en la `ReadyQueue` con su prioridad.
5. Extraer del queue hasta `available_slots` nodos.
6. Retornar `ScheduleDecision` con el lote.

**Callbacks de finalización:**

- `complete(node_name)` → marca completado, descubre nuevos nodos listos.
- `fail(node_name)` → marca fallido, retorna nodos bloqueados.
- `retry(node_name)` → encola para reintento.
- `skip(node_name)` → marca omitido, propaga transitivamente a dependientes.
- `cancel(node_name)` → marca cancelado.

### ParallelExecutor (`parallel_executor.py`)

Ejecuta múltiples nodos del grafo simultáneamente usando `asyncio`,
respetando la topología del DAG.

**`ParallelExecutorConfig`:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `max_parallel` | 4 | Nodos máximo en paralelo |
| `default_timeout` | 0.0 | Timeout por nodo (0 = sin límite) |
| `retry_policy` | `RetryPolicy()` | Política de reintentos por defecto |
| `pause_between_batches` | 0.0 | Pausa entre lotes en segundos |

**Flujo de ejecución (`execute()`):**

```
1. Reset scheduler + calcular niveles topológicos
2. Loop principal:
   a. schedule() → obtener próximo lote
   b. Si no hay lote y hay tareas activas → esperar FIRST_COMPLETED
   c. Si no hay lote ni tareas activas → break
   d. Para cada nodo del lote:
      - Transicionar: PENDING → READY → RUNNING
      - Crear asyncio.Task para _execute_node()
   e. Esperar FIRST_COMPLETED
   f. Procesar tareas completadas
   g. Pausa opcional entre lotes
3. Cancelar tareas restantes
4. Calcular métricas finales + camino crítico
5. Retornar ExecutionResult
```

**Manejo de reintentos en `_execute_node()`:**

```
while True:
    try:
        result = await executor_fn(name, node.to_dict())
        → transition: RUNNING → COMPLETED
        → scheduler.complete(name)
        return
    except CancelledError:
        → transition: CANCELLED
        → raise
    except Exception:
        retry_record = policy.record_attempt(...)
        if should_retry:
            → FAILED → RETRYING → READY
            → await policy.wait_for_retry()
            → scheduler.retry()
            continue
        else:
            → FAILED (exhausted)
            → scheduler.fail()
            return
```

### RetryPolicy (`retry_policy.py`)

Política de reintentos configurable con backoff exponencial y jitter.

**Configuración:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `max_retries` | 3 | Máximo de reintentos |
| `base_delay` | 0.5s | Delay base para el primer reintento |
| `max_delay` | 30.0s | Cap del delay |
| `exponential_base` | 2.0 | Base del backoff exponencial (2 = duplicar) |
| `jitter` | 0.1 | Factor de jitter (0 = ninguno, 1 = completo) |

**Fórmula de delay:**
```
delay = min(base_delay * exponential_base ^ attempt, max_delay)
delay = delay + uniform(-delay * jitter, +delay * jitter)
```

**Ejemplo de delays por intento (defaults):**

| Intento | Delay sin jitter | Rango con jitter |
|---------|------------------|------------------|
| 0 | 0.5s | 0.45s - 0.55s |
| 1 | 1.0s | 0.90s - 1.10s |
| 2 | 2.0s | 1.80s - 2.20s |

**Excepciones reintentables por defecto:**
`ConnectionError`, `TimeoutError`, `OSError`, `RuntimeError`.

### RecoveryManager (`recovery.py`)

Gestiona recuperación de fallos, re-planificación parcial, rollback y
operaciones de resume para grafos de ejecución.

**`RecoveryCheckpoint`:**

Snapshot del estado de ejecución: nodos completados, fallidos, omitidos,
resultados, errores, conteos de reintentos y estadísticas del scheduler.

**Operaciones de recuperación:**

| Método | Descripción |
|--------|-------------|
| `save_checkpoint(graph_id, stats)` | Guardar snapshot del estado actual |
| `recover_task(node_name, strategy)` | Recuperar un solo nodo fallido |
| `recover_plan()` | Reintentar todos los nodos fallidos |
| `rollback(checkpoint_id)` | Revertir a un checkpoint anterior |
| `resume()` | Identificar nodos pendientes y listos |
| `continue_from_failure(node_name)` | Continuar después de un fallo |
| `partial_replan(node_name, replan_fn)` | Re-planificar solo el subgrafo afectado |

**Estrategias de `recover_task`:**

- `"retry"`: resetear nodo a PENDING para re-ejecución.
- `"skip"`: marcar SKIPPED y propagar transitivamente a dependientes.
- `"replan"`: identificar subgrafo afectado para re-planificación.

**Rollback:**
- Nodos completados en el checkpoint se mantienen.
- Nodos fallidos/saltados se resetean a PENDING.
- Nodos en estados intermedios se resetean a PENDING.

**`continue_from_failure`:**
Cuando `skip_failed=True`, marca el nodo fallido y todo su subgrafo afectado
como SKIPPED, luego calcula qué nodos restantes aún pueden ejecutarse.

### ExecutionStateMachine (`state_machine.py`)

Máquina de estado centralizada para rastrear los estados de los nodos
de ejecución con validación de transiciones y log de historial.

**`StateTransition`:** registro individual de una transición con
`node_name`, `from_state`, `to_state`, `timestamp` y `metadata`.

**API:**

```python
sm = ExecutionStateMachine(max_history=1000)

sm.initialize(["task_a", "task_b", "task_c"])  # Todos a PENDING
sm.transition("task_a", ExecutionNodeState.READY)  # Con validación
sm.set_state("task_a", ExecutionNodeState.RUNNING, force=True)  # Sin validación
sm.get_state("task_a")          # → ExecutionNodeState.RUNNING
sm.validate_transition("task_a", ExecutionNodeState.COMPLETED)  # → True
sm.history(node_name="task_a")  # → list[StateTransition]
sm.all_states()                 # → dict[name, state]
sm.to_dict()                    # → Serialización
sm.reset()                      # Limpiar todo
```

### ReadyQueue (`ready_queue.py`)

Cola dinámica con prioridades para nodos listos para ejecutar.
Implementada como un heap de `(prioridad, contador, nombre)`.

**Características:**

- **Heap-based**: inserción O(log n), extracción O(log n).
- **Prioridad determinista**: desempate por contador de inserción (FIFO
  entre nodos de igual prioridad).
- **Deduplicación**: un nodo no se puede insertar dos veces.
- **Max size**: opcional, rechaza nodos cuando la cola está llena.
- **Mark completed/failed**: remove silencioso de la cola.

**API:**

```python
q = ReadyQueue(max_size=0)
q.push("task_a", priority=0)     # → True
q.push("task_b", priority=1)     # → True
q.pop()                           # → "task_a" (mayor prioridad)
q.next_ready()                    # → "task_b" (peek)
q.contains("task_b")              # → True
q.mark_completed("task_b")
q.size                            # → 0
q.empty                           # → True
q.to_list()                       # → []
```

## Eventos del Execution Graph

| Evento | Descripción |
|--------|-------------|
| `exec_graph.task.ready` | Nodo listo para ejecución |
| `exec_graph.task.running` | Nodo inició ejecución |
| `exec_graph.task.completed` | Nodo completado |
| `exec_graph.task.failed` | Nodo fallido |
| `exec_graph.task.retry` | Nodo en reintento |
| `exec_graph.task.skipped` | Nodo omitido |
| `exec_graph.plan.recovered` | Plan recuperado tras fallo |
| `exec_graph.plan.resumed` | Plan reanudado |
| `exec_graph.parallel.batch` | Lote paralelo despachado |
| `exec_graph.critical.path` | Camino crítico calculado |

## Flujo de Ejecución Paralela — Ejemplo

```
DAG:
  A ──→ C ──→ E
  B ──→ D ──┘
  B ──→ C

Niveles topológicos:
  Nivel 0: A, B  (raíces, sin dependencias)
  Nivel 1: C, D  (C depende de A y B; D depende de B)
  Nivel 2: E     (depende de C y D)

max_parallel = 2

Lote 1: [A, B]  → 2 nodos en paralelo (nivel 0)
  A completado, B completado
Lote 2: [C, D]  → 2 nodos en paralelo (nivel 1)
  C completado, D completado
Lote 3: [E]     → 1 nodo (nivel 2)
  E completado

Tiempo secuencial: 5 unidades
Tiempo paralelo:   3 unidades (3 lotes)
```