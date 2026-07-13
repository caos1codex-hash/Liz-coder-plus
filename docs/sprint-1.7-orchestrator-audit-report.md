# Sprint 1.7 — Auditoría del Orchestrator

> Informe técnico de auditoría previa a la refactorización del
> Orchestrator en Sprint 1.7. Versión auditada: `v0.1.2` (Sprint 1.6).

## 1. Objetivo

Esta auditoría revisa el estado actual del `Orchestrator` y de los
módulos que lo rodean para detectar puntos de mejora, código
duplicado, riesgos y oportunidades de unificación antes de
implementar el pipeline de ejecución unificado, el sistema de
tareas, el motor de workflows y el planner.

## 2. Alcance

Se revisaron los siguientes módulos:

- `packages/core/src/orchestrator.py`
- `packages/core/src/agent_router.py`
- `packages/core/src/event_bus.py`
- `packages/core/src/events.py`
- `packages/core/src/tool_executor.py`
- `packages/core/src/session_manager.py`
- `packages/core/src/protocols.py`
- `packages/core/src/ws_manager.py` (sin cambios relevantes)
- `packages/agents/src/base.py`
- `packages/agents/src/registry.py`
- `packages/tools/src/registry.py`
- `packages/tools/src/base.py`
- `packages/memory/src/memory/manager.py`

## 3. Flujo de ejecución actual

```
Usuario (WebSocket)
  │
  ▼
WS Routes (apps/backend/src/api/ws_routes.py)
  │
  ▼
Orchestrator.handle_message(message, session_id, mode)
  │
  ├─ emit USER_MESSAGE_RECEIVED
  ├─ SessionManager.set_mode + append_turn(role="user")
  ├─ _build_context(session_id)
  ├─ AgentRouter.route(message, context)
  ├─ emit AGENT_INVOKED
  ├─ asyncio.wait_for(agent.handle(message, context), timeout)
  │       ├─ OK → emit AGENT_COMPLETED
  │       └─ Error / Timeout → emit AGENT_FAILED
  ├─ SessionManager.append_turn(role="assistant" | "system")
  │
  ▼  Envelope {type, content, status, agent_name, duration_ms}
WebSocket → Cliente
```

El Orchestrator también expone `execute_tool()` y `stream_message()`,
que se consideran puntos de extensión correctos.

## 4. Dependencias observadas

```
Orchestrator
  ├── WebSocketManager
  ├── SessionManager ─── (opcional) MemoryManager ─── SQLite
  ├── AgentRouter ─── EchoAgent (default)
  ├── EventBus
  └── ToolExecutor ─── PermissionService (opcional)
```

No hay dependencias circulares. La importación lazy de `MemoryManager`
es correcta.

## 5. Hallazgos

### 5.1 Puntos fuertes

- **Separación de responsabilidades clara:** cada módulo tiene un rol
  bien definido. El `Orchestrator` orquesta; el `AgentRouter` decide;
  el `SessionManager` mantiene el historial; el `ToolExecutor` ejecuta
  herramientas con permisos.
- **EventBus desacoplado:** todos los componentes publican eventos al
  bus. Los consumidores pueden suscribirse sin acoplarse a los
  productores.
- **Validación por Protocol:** tanto agentes como herramientas son
  validados con `runtime_checkable` Protocol, evitando errores en
  runtime.
- **Lifecycle consistente:** `BaseAgent` y `BaseTool` comparten
  patrón de estados (`CREATED → READY → RUNNING → COMPLETED/FAILED →
  SHUTDOWN`).
- **Tolerancia a fallos:** los handlers del EventBus se aíslan; un
  handler roto no rompe a los demás.
- **Timeout configurable:** agentes y herramientas tienen timeout
  configurable con cancelación explícita de la tarea subyacente.

### 5.2 Código duplicado y anti-patrones

| Hallazgo | Ubicación | Severidad |
|---|---|---|
| Pipeline de manejo de mensajes monolítico en `handle_message` (~180 líneas) | `orchestrator.py:294-474` | Media |
| Construcción del contexto mezclada con persistencia | `orchestrator.py:336-342` | Baja |
| Errores manejados con tres bloques `except` repetidos | `orchestrator.py:378-456` | Media |
| `agent_status()` mezcla metadata de BaseAgent con objetos simples sin normalizar | `orchestrator.py:265-277` | Baja |
| No existe concepto de "tarea": cada mensaje es procesado de forma atómica | `orchestrator.py` (general) | Alta |
| `stream_message` vuelve a llamar a `handle_message` sin reutilizar el pipeline | `orchestrator.py:509-560` | Baja |
| `ToolExecutor` mantiene `_active_tasks` por `tool_name`, lo que impide ejecuciones paralelas del mismo tool | `tool_executor.py:141, 231` | Media |

### 5.3 Riesgos

1. **No hay noción de "tarea" formal.** Un mensaje se procesa en una
   sola llamada; no es posible pausar, reanudar, cancelar ni consultar
   el progreso de un proceso en marcha. Esto bloquea capacidades
   futuras como generación de código multi-paso, búsquedas largas o
   workflows compuestos.
2. **No hay motor de workflows.** No se puede expresar "ejecuta A,
   luego B, y si B falla, ejecuta C". Cada invocación al
   Orchestrator es independiente.
3. **No hay planner.** La decisión de qué agente y herramientas usar
   es estática (reglas en `AgentRouter`). No existe un componente que
   divida una tarea grande en subtareas.
4. **Observabilidad limitada.** Aunque se emiten eventos, no hay
   métricas estructuradas (duración por agente, tasa de errores,
   reintentos, cancelaciones). Los logs son informativos pero no
   consultables.
5. **Cancelación limitada a herramientas.** No se puede cancelar
   un mensaje en curso desde la API pública del Orchestrator (sólo
   por timeout implícito).
6. **Memoria no consultada durante el planning.** El contexto que se
   pasa al agente incluye el historial de la sesión, pero no hay
   recuperación semántica ni búsqueda por sesiones pasadas.
7. **`_active_tasks` por nombre de tool** provoca colisión si dos
   agentes invocan simultáneamente el mismo tool (uno sobrescribe
   el task del otro en el dict).

### 5.4 Escalabilidad

| Dimensión | Estado | Comentario |
|---|---|---|
| Sesiones concurrentes | OK | SessionManager es dict en RAM; scales linealmente |
| Mensajes por segundo | Limitado | Cada `handle_message` es await secuencial; no hay batching |
| Agentes registrados | OK | Dict; registro O(1) |
| Herramientas paralelas | Bloqueado | `_active_tasks` por nombre impide ejecuciones paralelas |
| Eventos concurrentes | OK | EventBus invoca handlers secuencialmente pero sin lock global |
| Persistencia | OK | SQLite con WAL configurable; cache RAM por sesión |

## 6. Cuellos de botella

1. **`handle_message` es estrictamente secuencial.** Cada paso
   (emisión de eventos, persistencia, routing, ejecución del agente,
   segunda persistencia) se ejecuta uno detrás de otro. Para casos
   donde el agente tarda (LLM, llamada a API), el cuello es el
   agente en sí, pero los pasos previos y posteriores suman latencia
   innecesaria cuando podrían solaparse.
2. **`SessionManager.append_turn` persiste síncronamente al final.**
   No hay ack diferido: la respuesta al usuario espera a que
   SQLite confirme el `INSERT`. Con SQLite esto es rápido, pero en
   backends más lentos (PostgreSQL remoto) sería un problema.
3. **`AgentRouter.route` evalúa reglas una a una.** Si hay muchas
   reglas, cada mensaje paga O(N). No es crítico hoy (pocas reglas)
   pero conviene documentar.
4. **`stream_message` ejecuta el agente completo y luego trocea la
   respuesta.** No es streaming real; Sprint 5 lo reemplazará con
   streaming nativo del LLM.

## 7. Oportunidades de unificación

### 7.1 Pipeline unificado

Reemplazar el método `handle_message` monolítico por un pipeline
explícito:

```
Request → Validate → BuildContext → RetrieveMemory → Plan →
SelectAgent → CheckPermissions → Execute → UpdateMemory →
PublishEvents → Respond
```

Cada etapa es una corrutina independiente, con su propio manejo de
errores y eventos. El Orchestrator orquesta la cadena pero delega
la lógica en etapas reutilizables.

### 7.2 Task System

Introducir `Task` como unidad de trabajo direccionable:

- Una `Task` puede ser un mensaje, una subtarea generada por el
  planner, o un paso de un workflow.
- Tiene lifecycle explícito (`PENDING → READY → RUNNING →
  WAITING → COMPLETED/FAILED/CANCELLED`).
- El `TaskManager` mantiene el registro, permite cancelar,
  pausar, reanudar y consultar estado/historial.

### 7.3 Workflow Engine

Un `Workflow` es un DAG de `Task`s con dependencias explícitas.
El `WorkflowManager` se encarga de:

- Validar el DAG (sin ciclos).
- Ejecutar tareas según sus dependencias (secuenciales o
  paralelas).
- Reintentar tareas fallidas.
- Cancelar el workflow completo si una tarea crítica falla.
- Recuperar workflows interrumpidos (persistencia futura).

### 7.4 Planner

El `Planner` descompone una tarea grande en subtareas y decide
qué agente y herramientas usa cada una. Aprovecha:

- `MemoryManager` (para contexto histórico).
- `AgentRegistry` (para conocer capacidades).
- `ToolRegistry` (para conocer herramientas disponibles).

Hoy el Planner es stub-friendly: su primera implementación será
heurística (reglas), dejando la puerta abierta a un Planner basado
en LLM en Sprint 2.

### 7.5 Observabilidad

Agregar un componente `MetricsCollector` que escuche eventos del
EventBus y mantenga contadores/histogramas:

- `tasks_total`, `tasks_completed`, `tasks_failed`, `tasks_cancelled`.
- `task_duration_ms` (histograma por agente).
- `tool_duration_ms` (histograma por herramienta).
- `workflow_duration_ms`.
- `retries_total`, `errors_by_type`.

Los logs estructurados se enriquecen con `task_id`, `workflow_id`
y `correlation_id` para trazabilidad.

## 8. Recomendaciones por fase

| Fase | Acción | Justificación |
|---|---|---|
| 1 | Generar este informe | ✅ |
| 2 | Implementar `ExecutionPipeline` con etapas explícitas | Reduce acoplamiento, facilita tests |
| 3 | Implementar `Task` + `TaskManager` | Base para workflows y planner |
| 4 | Implementar `Workflow` + `WorkflowManager` | Permite tareas multi-paso |
| 5 | Implementar `Planner` | Automatiza división de trabajo |
| 6 | Agregar `MetricsCollector` + eventos extendidos | Observabilidad real |
| 7 | Tests de pipeline, tasks, workflows, planner, observabilidad | Cobertura |
| 8 | Actualizar `docs/architecture.md` | Documentación viva |
| 9 | Validación final + informe | Cierre del sprint |

## 9. Riesgos pendientes (a gestionar durante el sprint)

1. **Compatibilidad hacia atrás:** `handle_message` es API pública.
   Debe mantenerse; el pipeline nuevo será interno y se invocará
   desde `handle_message`.
2. **Performance:** añadir etapas añade overhead. Medir antes
   de optimizar.
3. **Complejidad de tests:** el pipeline trae más puntos de fallo.
   Se justifica con la fase de testing (Fase 7).
4. **Memoria del TaskManager:** si no se limpian tareas completadas,
   el dict crece indefinidamente. Implementar TTL o limpieza por
   lotes.
5. **Workflows muy grandes:** el DAG se mantiene en RAM. Para
   workflows de cientos de nodos, considerar persistencia (futuro
   sprint).

## 10. Próximos pasos

1. Implementar el `ExecutionPipeline` (Fase 2) preservando
   `Orchestrator.handle_message` como punto de entrada público.
2. Introducir `Task` y `TaskManager` (Fase 3) sin romper la API
   existente.
3. Encadenar tareas vía `WorkflowManager` (Fase 4).
4. Agregar el `Planner` (Fase 5) como componente opcional invocado
   desde el pipeline.
5. Cablear observabilidad (Fase 6) sin modificar APIs públicas.
6. Cobertura de tests (Fase 7).
7. Documentar (Fase 8).
8. Cierre (Fase 9).

---

**Fecha:** 2026-07-14
**Autor:** Sprint 1.7 — Auditoría Automática
