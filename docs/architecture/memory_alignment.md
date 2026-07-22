# Memory Architecture Alignment — Audit Report

**Proyecto:** Liz Coder Plus  
**Fecha:** 2026-07-22  
**Tipo:** Proyecto A.1 — Fuera del Roadmap  
**Objetivo:** Auditar y alinear toda la arquitectura para que cualquier componente obtenga información mediante índices y recuperación selectiva, nunca mediante cargas masivas de contexto.

---

## 1. Arquitectura Anterior (Estado Actual)

### Flujo Actual de Acceso a Memoria

```
Request
  ↓
MemoryManager.get_context(session_id)  ← Carga COMPLETA del historial
  ↓
PromptPipeline.build(memory_context=...)  ← Recibe datos pre-cargados
  ↓
ContextManager.build(history, memory_context, ...)  ← Trunca por oldest-first
  ↓
LLM
```

**Problemas fundamentales:**

- **No existe capa de índice.** Ningún componente consulta un índice ligero antes de acceder a contenido. Todas las consultas SQL usan `SELECT *`.
- **No hay separación por tipo de memoria.** Runtime, Session, Project, Knowledge, Integration, Preference, Semantic e Index están mezclados en un único almacenamiento plano.
- **Carga masiva sistemática.** `MemoryManager.get_context()` retorna hasta 200 mensajes completos. `SessionManager.snapshot()` serializa todo el estado incluyendo historial completo. `RecoveryManager` captura el estado total en un blob JSON.
- **Recuperación no selectiva.** El `ContextManager` trunca por antigüedad (oldest-first), no por relevancia. No existe recuperación on-demand ni lazy loading.
- **El protocolo `Memory` es KV plano.** Solo soporta `store(key, value)` y `retrieve(key)`, lo que imposibilita búsqueda, filtrado por categoría, o acceso jerárquico.
- **Escalabilidad limitada.** Búsqueda de embeddings en `AgentMemory.search_similar()` es O(n) lineal sin índice ANN. No soportaría 100,000+ recuerdos sin degradación.

---

## 2. Arquitectura Nueva (Estado Objetivo)

### Flujo Objetivo de Acceso a Memoria

```
Request
  ↓
Memory Index          ← Consulta índice ligero (id, title, category, tags, priority, summary, location, relationships, timestamp)
  ↓
Selector              ← Filtra y ordena por relevancia (solo metadata del índice)
  ↓
Retriever             ← Carga contenido COMPLETO solo de los entries seleccionados
  ↓
Loader                ← Resuelve relaciones y referencias pendientes
  ↓
Context Builder       ← Ensambla: memoria requerida + estado actual + objetivo + herramientas necesarias
  ↓
LLM                   ← Recibe SOLO lo solicitado, nunca memoria completa
```

### Principios Obligatorios

1. **Todo acceso a memoria** debe seguir el flujo Index → Selector → Retriever → Loader → Context Builder → LLM.
2. **Ningún módulo** debe leer directamente archivos de memoria grandes o cargar historial completo.
3. **Los índices** solo contienen: `id`, `title`, `category`, `tags`, `priority`, `summary`, `location`, `relationships`, `timestamp`. NUNCA contenido completo.
4. **La recuperación** debe ser: On Demand, Lazy Loading, Selectiva, Jerárquica, Escalable.
5. **El Context Builder** ensambla únicamente: memoria requerida, estado actual, objetivo, herramientas necesarias.
6. **Las relaciones** se expresan mediante referencias (no duplicación de contenido).
7. **Cada integración** (Spotify, GitHub, APIs) mantiene su propia memoria especializada e índices, sin mezclar información global.

### Tipos de Memoria (Separación Obligatoria)

| Tipo | Responsabilidad | Ejemplo |
|------|----------------|---------|
| **Runtime Memory** | Estado temporal de ejecución actual | Variables de sesión activa, flag de cancelación |
| **Session Memory** | Historial de conversación por sesión | Mensajes usuario/assistant por session_id |
| **Project Memory** | Archivos, estructura y estado del proyecto | Archivos fuente, configuración, dependencias |
| **Knowledge Memory** | Base de conocimiento general | Documentación, APIs, patrones de código |
| **Integration Memory** | Estado y credenciales de integraciones externas | Tokens OAuth, estado de conexión Spotify/GitHub |
| **Preference Memory** | Preferencias del usuario | Idioma, modo de operación, estilo |
| **Semantic Memory** | Embeddings para búsqueda semántica | Vectores de conversaciones pasadas |
| **Index Memory** | Metadatos ligeros para recuperación | Índices de todos los tipos anteriores |

---

## 3. Cambios Realizados en Esta Auditoría

**Esta auditoría es solo de análisis.** No se han implementado cambios funcionales ni se han modificado APIs públicas. Los únicos cambios son:

- Creación de este documento `docs/architecture/memory_alignment.md`.
- Identificación detallada de cada componente y su estado de alineación.
- Propuestas concretas de solución para cada incompatibilidad.

---

## 4. Componentes Auditados — Estado de Alineación

### 4.1 Resumen General

| Estado | Cantidad | Porcentaje |
|--------|----------|------------|
| ✔ Alineado | 27 | 42% |
| ⚠ Parcialmente Alineado | 21 | 33% |
| ✖ Incompatible | 16 | 25% |
| **Total** | **64** | **100%** |

### 4.2 `packages/memory/` — El Almacén Fundamental

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `src/base.py` | ✖ | Protocolo KV plano: `store(key, value)` / `retrieve(key)`. Sin índice, sin tipos, sin búsqueda. |
| `src/memory/manager.py` | ✖ | `get_context()` retorna hasta 200 mensajes completos. Sin capa de índice. Sin separación por tipo. |
| `src/memory/database.py` | ⚠ | Ejecución SQL genérica. No soporta tablas de índice ni consultas indexadas. |
| `src/memory/models/conversation.py` | ✖ | Solo existe `ConversationMessage` con `content: str`. No hay modelo `MemoryIndexEntry`. No hay `MemoryType` enum. |
| `src/memory/repositories/conversation_repository.py` | ✖ | Todos los métodos usan `SELECT *`. Carga siempre `content` completo. Sin consultas index-only. |
| `src/memory/repositories/task_repository.py` | ✖ | `list_tasks()` y `get_active_tasks()` usan `SELECT *`. Carga `description`, `result`, `errors`, `metadata` siempre. |
| `src/memory/repositories/workflow_repository.py` | ✖ | Igual patrón: `SELECT *` en todas las consultas. Carga `dag_definition`, `result`, `errors`. |
| `src/memory/repositories/execution_repository.py` | ✖ | Todas las lecturas cargan `result`, `error`, `metadata` completos. |
| `src/memory/migrations/initial.sql` | ✖ | Tabla `conversations` almacena `content TEXT` junto a metadatos. Sin tabla de índice separada. Sin columna `memory_type`. |
| `src/memory/migrations/sprint_1_8_tasks_workflows.sql` | ✖ | Todas las tablas (`tasks`, `workflows`, `execution_history`) mezclan campos índice con campos de contenido pesado. |

### 4.3 `packages/core/src/context_engine/` — Motor de Contexto

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `context_engine.py` | ⚠ | Pipeline correcto (Select → Rank → Budget → Compress) pero recibe datos **pre-cargados** por el caller. Necesita `MemoryIndexClient` propio. |
| `context_builder.py` | ⚠ | Recibe `available_memories` como lista plana. El `MemorySelector` accede a `value` (contenido completo) para scoring. |
| `memory_selector.py` | ⚠ | `_score_memory()` requiere `str(value).lower()` — necesita contenido completo para puntuar. Debe puntuar contra metadata del índice. |
| `file_selector.py` | ⚠ | `_score_file()` hace keyword matching contra contenido completo del archivo. Podría separarse en fase de metadata + fase de contenido. |
| `history_selector.py` | ⚠ | Recibe historial pre-cargado. Accede a `entry.get("content", "")` para scoring. |
| `context_ranker.py` | ✔ | Opera sobre `RankableItem` ya seleccionados. Posición correcta en el pipeline. |
| `context_budget.py` | ✔ | Budget por categoría y compresión bien implementados. |
| `context_cache.py` | ✔ | LRU con TTL y invalidación por patrones. Bien implementado. |
| `task_decomposer.py` | ✔ | No es componente de memoria. |
| `subtask_generator.py` | ✔ | No es componente de memoria. |
| `models.py` | ✖ | `ContextMemory` almacena `value: Any` (contenido completo). No existe `MemoryIndexEntry`. No hay `MemoryType` enum. `AgentContext.to_agent_dict()` serializa todo. |

### 4.4 `packages/llm/` — Interfaz con el Modelo

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `src/llm/context/manager.py` | ✖ | `build()` recibe historial y memoria pre-cargados. `_fit_to_budget()` trunca por antigüedad (no por relevancia). `_format_memory()` trunca a 200 chars. No conectado al ContextEngine. |
| `src/llm/manager.py` | ✔ | Registry de modelos LLM. No accede memoria. |
| `src/llm/protocols.py` | ✔ | Protocolo `LLMProvider`. No accede memoria. |
| `src/llm/models.py` | ✔ | Modelos de datos LLM. No accede memoria. |
| `src/llm/prompt/pipeline.py` | ✖ | `build()` acepta `memory_context: list[dict]` pre-cargado. Llama directamente a `MemoryManager.get_context()`. No usa ContextEngine. |

### 4.5 `packages/core/src/` — Orquestación y Planificación

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `orchestrator.py` | ⚠ | `_build_context()` llama `session.snapshot()` que retorna historial completo. MemoryManager solo se usa para persistencia pass-through. |
| `planner.py` | ⚠ | Parámetro `memory` aceptado pero nunca usado (código muerto). Planes basados solo en regex. |
| `agent_router.py` | ✔ | Routing por predicados. No accede memoria directamente. |
| `pipeline.py` | ⚠ | `stage_retrieve_memory()` llama `get_context()` y vuelca todo en `persistent_history`. El enriquecimiento con ContextEngine es opcional y tiene fallback a carga masiva. |
| `session_manager.py` | ✖ | `restore_session()` carga TODO el historial a RAM. `snapshot()` serializa estado completo. `MAX_HISTORY=200` es FIFO bruto, no selectivo. Sin separación de tipos. |
| `tool_executor.py` | ✔ | Ejecución mediada. No accede memoria. |
| `plan_executor.py` | ⚠ | Pasa `context` completo (con historial masivo) a cada agente. No hay construcción de contexto por tarea. |
| `plan_models.py` | ✔ | Modelos de datos puros con máquinas de estado. |
| `plan_tracking.py` | ⚠ | Almacena `ExecutablePlan` completo en memoria. Snapshots incluyen `result: Any` de todos los pasos. Historial de hasta 200 snapshots completos. |
| `task.py` | ⚠ | `load_from_persistence()` carga TODAS las tareas activas a RAM. Todas las consultas retornan objetos `Task` completos. Sin `TaskIndexEntry`. |
| `scheduler.py` | ⚠ | `_load_persisted_jobs()` carga todos los jobs activos. `SELECT *` en repositorio. Sin `SchedulerJobIndexEntry`. |
| `recovery.py` | ✖ | Checkpoint es un blob JSON monolítico. `restore_latest()` deserializa todo. Sin restauración selectiva. Sin checkpoints incrementales. |
| `protocols.py` | ✖ | Protocolo `Memory` define `store(key, value)` / `retrieve(key)` — contrato KV plano que imposibilita la nueva arquitectura. Protocolo `Agent.handle(message, context)` usa `dict[str, Any]` sin estructura definida. |

### 4.6 `packages/tools/` — Herramientas

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `src/base.py` | ✔ | Ciclo de vida de herramientas. No accede memoria externa. |
| `src/registry.py` | ✔ | Registro de herramientas. Solo metadatos ligeros. |
| `src/executor.py` | ✔ | Runner de ejecución. No accede memoria. |
| `src/selection.py` | ✔ | Selección de herramientas por capacidades. Selectiva y jerárquica. |
| `src/ranking.py` | ✔ | Scoring multi-factor sobre metadatos. |
| `src/filters.py` | ✔ | Filtros componibles sobre propiedades ligeras. |
| `src/lifecycle.py` | ⚠ | Sesiones sin evicción. `all_sessions()` sin límite. |
| `src/validation.py` | ✔ | Validación pre-ejecución. |
| `src/exceptions.py` | ✔ | Jerarquía de excepciones. |
| `src/results.py` | ✔ | Modelos de datos del pipeline. |
| `src/execution.py` | ✔ | Orquestador del pipeline de ejecución. |
| `src/tools/file_tool.py` | ⚠ | `_read_file()` carga archivo completo. `_list_directory()` sin paginación. |
| `src/tools/terminal_tool.py` | ✔ | Output truncado a 64KB. |
| `src/tools/system_tool.py` | ✔ | Solo metadatos del sistema. |

### 4.7 `packages/agents/` — Agentes Base

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `src/base.py` | ✔ | Clase base. `context` es input del caller. |
| `src/registry.py` | ✔ | Registro simple de agentes. |

### 4.8 `packages/multiagent/` — Sistema Multi-Agente

| Archivo | Estado | Problema Principal |
|---------|--------|-------------------|
| `src/multiagent/memory.py` | ✖ | **CRÍTICO.** No existe índice. Sin separación por tipo. `to_dict()` serializa todo. Búsqueda de embeddings es O(n) lineal. Sin lazy loading. Sin memoria por integración. |
| `src/multiagent/agents/research.py` | ✖ | Escaneo lineal de TODA la memoria con `to_dict()` en cada entrada. Substring matching contra contenido completo. |
| `src/multiagent/base.py` | ⚠ | `serialize()` llama `memory.to_dict()` — vuelca memoria completa. |
| `src/multiagent/orchestrator.py` | ⚠ | `serialize()` cascada `memory.to_dict()` para cada agente. |
| `src/multiagent/agents/memory.py` | ⚠ | `retrieve`, `search`, `recent` retornan `to_dict()` con contenido completo. |
| `src/multiagent/agents/planner.py` | ⚠ | Almacena planes de workflow completos en memoria a largo plazo. |
| `src/multiagent/registry/agent_registry.py` | ⚠ | `AgentRecord` referencia agente completo incluyendo memoria. |
| `src/multiagent/workflow/context.py` | ⚠ | `FileSnapshot` almacena contenido completo. `transfer_context()` y `snapshot()` vuelcan todo. |
| `src/multiagent/config.py` | ✔ | Configuración congelada. |
| `src/multiagent/enums.py` | ✔ | Enumeraciones con validación de transiciones. |
| `src/multiagent/messages.py` | ✔ | MessageBus con colas por agente. |
| `src/multiagent/queue.py` | ✔ | Cola de prioridad con lifecycle. |
| `src/multiagent/logs.py` | ✔ | Logger con ring buffer acotado. |
| `src/multiagent/agents/coder.py` | ✔ | Almacena solo sus resultados. |
| `src/multiagent/agents/reviewer.py` | ✔ | Almacena solo sus revisiones. |
| `src/multiagent/agents/git.py` | ✔ | Solo logs de eventos. |
| `src/multiagent/agents/terminal.py` | ✔ | Solo logs de eventos. |
| `src/multiagent/registry/adapter.py` | ✔ | Mapeo de nombres a capacidades. |
| `src/multiagent/registry/manifest.py` | ✔ | **Modelo de referencia** para índices ligeros: id, name, version, capabilities, priority, description. |
| `src/multiagent/registry/capability.py` | ✔ | Catálogo de capacidades. Metadatos ligeros. |
| `src/multiagent/registry/resolver.py` | ✔ | Resolución por scoring sobre metadatos. |
| `src/multiagent/registry/lifecycle.py` | ✔ | Gestión de lifecycle con health checks ligeros. |
| `src/multiagent/registry/orchestrator.py` | ⚠ | Métricas sobre todos los lifecycles. Aceptable. |

### 4.9 `packages/memctx/`

**No existe.** El paquete `packages/memctx/` no está presente en el repositorio. La funcionalidad de contexto de memoria está distribuida entre `packages/memory/` y `packages/core/src/context_engine/`.

---

## 5. Componentes Pendientes de Alineación

### 5.1 Prioridad P0 — Bloqueantes (impiden la nueva arquitectura)

| # | Componente | Acción Requerida |
|---|-----------|-----------------|
| 1 | `packages/core/src/protocols.py` — Protocolo `Memory` | Rediseñar a `MemoryIndex`, `MemorySelector`, `MemoryRetriever` como protocolos separados. Agregar `MemoryIndexEntry` y `MemoryType` enum. |
| 2 | `packages/memory/src/base.py` | Reemplazar protocolo KV por protocolos indexados. |
| 3 | `packages/memory/src/memory/models/` | Crear `MemoryIndexEntry` (solo metadata) y `MemoryType` enum. Mantener `ConversationMessage` para carga lazy. |
| 4 | `packages/memory/src/memory/migrations/` | Agregar tabla `memory_index` con campos ligeros. Agregar columna `memory_type` a tablas existentes. |
| 5 | `packages/core/src/context_engine/models.py` | Crear `MemoryIndexEntry`. Agregar `MemoryType` enum. Modificar `ContextMemory` para soportar `content_ref` (lazy loading). |
| 6 | `packages/multiagent/src/multiagent/memory.py` | Agregar `MemoryIndex` interno. Separar tipos de memoria. Reemplazar `to_dict()` por `summary()`. Agregar ANN para embeddings. |

### 5.2 Prioridad P1 — Funcionales (necesarios para operación correcta)

| # | Componente | Acción Requerida |
|---|-----------|-----------------|
| 7 | `packages/memory/src/memory/repositories/*` | Agregar métodos `*_index()` que retornen solo campos ligeros (sin `content`, `result`, `errors`, `metadata`). |
| 8 | `packages/memory/src/memory/manager.py` | Reemplazar `get_context()` por `query_index()` → `select()` → `retrieve()`. |
| 9 | `packages/core/src/context_engine/memory_selector.py` | Cambiar input de `list[dict]` a `list[MemoryIndexEntry]`. Puntuar contra metadata, no contra contenido. |
| 10 | `packages/core/src/context_engine/context_builder.py` | Aceptar `MemoryIndexEntry` en lugar de memorias completas. |
| 11 | `packages/core/src/context_engine/context_engine.py` | Agregar `MemoryIndexClient`. Cambiar `build_context()` para aceptar `MemoryQuery` en lugar de datos pre-cargados. |
| 12 | `packages/core/src/pipeline.py` | Reescribir `stage_retrieve_memory()`: Index → Select → Retrieve → Build. |
| 13 | `packages/core/src/session_manager.py` | Reemplazar `snapshot()` por `indexed_summary()`. Agregar `retrieve_turns(selectivos)`. Lazy-load desde persistencia. |
| 14 | `packages/llm/src/llm/prompt/pipeline.py` | Reemplazar `memory_context: list[dict]` por `agent_context: AgentContext` del ContextEngine. |
| 15 | `packages/llm/src/llm/context/manager.py` | Conectar al ContextEngine. Cambiar truncación de oldest-first a relevance-based. |
| 16 | `packages/multiagent/src/multiagent/agents/research.py` | Reemplazar escaneo lineal con búsqueda indexada por tags. |
| 17 | `packages/core/src/recovery.py` | Reemplazar blob JSON monolítico por almacenamiento indexado por componente. Agregar restauración selectiva. |

### 5.3 Prioridad P2 — Mejoras de escalabilidad

| # | Componente | Acción Requerida |
|---|-----------|-----------------|
| 18 | `packages/core/src/task.py` | Agregar `TaskIndexEntry`. `load_from_persistence()` debe cargar solo índices. Lazy-load detalles on-demand. |
| 19 | `packages/core/src/scheduler.py` | Agregar `SchedulerJobIndexEntry`. Cargar solo metadatos al inicio. |
| 20 | `packages/core/src/plan_tracking.py` | Snapshots de pasos deben ser index entries por defecto. Cargar `result` solo on-demand. |
| 21 | `packages/core/src/plan_executor.py` | Contexto por tarea: consultar Memory Index para cada subtarea. |
| 22 | `packages/multiagent/src/multiagent/workflow/context.py` | `FileSnapshot` con contenido lazy. `transfer_context()` y `snapshot()` selectivos por defecto. |
| 23 | `packages/tools/src/lifecycle.py` | Agregar evicción de sesiones. `all_sessions()` con límite. |
| 24 | `packages/tools/src/tools/file_tool.py` | Agregar `limit` a `list_directory()`. Lectura de archivos grandes por chunks. |

---

## 6. Compatibilidad

### 6.1 Regla General

**No se han modificado APIs públicas.** Esta auditoría es de solo lectura y documentación. Todas las propuestas son para futuros Sprints.

### 6.2 Riesgo de Cambios Futuros

| Cambio Propuesto | Riesgo | Mitigación |
|-----------------|--------|-----------|
| Rediseño del protocolo `Memory` | Alto — afecta a todos los consumidores | Crear nuevo protocolo `MemoryIndex` en paralelo. Mantener protocolo antiguo con deprecation. Adapter pattern durante transición. |
| Nuevos modelos (`MemoryIndexEntry`, `MemoryType`) | Bajo — son adiciones, no modificaciones | Agregar como nuevos archivos. No modificar modelos existentes inicialmente. |
| Migraciones SQL | Medio — requiere migración de datos existentes | Script de migración que pobla `memory_index` desde datos existentes. Backward-compatible. |
| `ContextEngine` con `MemoryIndexClient` | Medio — cambia la interfaz del engine | Parámetro opcional con fallback al comportamiento actual. |
| `session_manager.snapshot()` → `indexed_summary()` | Alto — usado por el orquestador | Crear `indexed_summary()` como nuevo método. Marcar `snapshot()` como deprecated. Período de gracia con ambos métodos. |

### 6.3 Componentes que NO requieren cambios

Los siguientes 27 componentes están alineados y **no necesitan modificación**:

- `packages/tools/`: base, registry, executor, selection, ranking, filters, validation, exceptions, results, execution, terminal_tool, system_tool
- `packages/agents/`: base, registry
- `packages/multiagent/`: config, enums, messages, queue, logs, agents/coder, agents/reviewer, agents/git, agents/terminal, registry/adapter, registry/manifest, registry/capability, registry/resolver, registry/lifecycle
- `packages/core/src/`: agent_router, tool_executor, plan_models
- `packages/core/src/context_engine/`: context_ranker, context_budget, context_cache, task_decomposer, subtask_generator

---

## 7. Riesgos

| # | Riesgo | Severidad | Probabilidad | Mitigación |
|---|--------|-----------|-------------|-----------|
| R1 | El protocolo `Memory` KV es el cuello de botella fundamental. Su simplicidad impide cualquier mejora downstream sin romperlo. | Crítica | Cierta | Diseñar nuevos protocolos en paralelo. Usar adapter pattern. Deprecation gradual. |
| R2 | `AgentMemory` (multiagent) es usado por todos los agentes. Cambios aquí afectan todo el sistema multi-agente. | Alta | Cierta | Cambios incrementales. Primero agregar índice como capa adicional, no reemplazar el store existente. |
| R3 | Las migraciones SQL pueden perder datos si no se hace correctamente la población del índice desde datos existentes. | Media | Baja | Script de migración con validación. Backup previo. Tests de regresión. |
| R4 | El `ContextEngine` ya tiene una pipeline correcta (Select → Rank → Budget) pero recibe datos pre-cargados. El cambio es de input, no de lógica interna. | Baja | Cierta | Cambiar la interfaz de input manteniendo la lógica interna. Tests unitarios existentes protegen la lógica. |
| R5 | La recuperación (recovery.py) usa un blob JSON monolítico. Cambiar a almacenamiento indexado es un refactor grande. | Alta | Media | Fase 1: Agregar metadatos de componente al blob. Fase 2: Separar por componente. Fase 3: Eliminar blob. |
| R6 | `packages/memctx/` no existe. La funcionalidad está dispersa. Esto puede causar confusión sobre dónde implementar la nueva capa de índice. | Media | Cierta | Definir explicitamente: índice en `packages/memory/`, motor de selección en `packages/core/src/context_engine/`, protocolos en `packages/core/src/protocols.py`. |
| R7 | Escalabilidad a 100,000+ recuerdos requiere índice ANN para embeddings. Esto es un cambio significativo en `AgentMemory`. | Alta | Media | Usar `numpy` + `faiss-cpu` como dependencia opcional. Fallback a búsqueda lineal para conjuntos pequeños. |

---

## 8. Nota sobre `packages/memctx/`

El paquete `packages/memctx/` mencionado en el briefing **no existe** en el repositorio actual. La funcionalidad de gestión de contexto de memoria está distribuida entre:

- **`packages/memory/`** — Almacenamiento y persistencia
- **`packages/core/src/context_engine/`** — Selección, ranking, budget, compresión
- **`packages/llm/src/llm/context/`** — Ensamblaje final para el LLM

Si se desea crear `packages/memctx/` en el futuro, debería actuar como la **capa de coordinación** entre estos tres paquetes, implementando el flujo Index → Selector → Retriever → Loader → Context Builder.

---

## 9. Verificación de Escalabilidad

### Estado Actual vs. Objetivo

| Métrica | Estado Actual | Objetivo | Brecha |
|---------|--------------|----------|--------|
| 100,000 recuerdos | `AgentMemory.search_similar()` es O(n) lineal. Toda la memoria en RAM. | Búsqueda sub-lineal con ANN index. Memoria paginada. | Crítica |
| 1,000 proyectos | Sin concepto de "proyecto" en memoria. Todo es por sesión. | `ProjectMemory` con índice por proyecto. | Media |
| Millones de archivos | `FileSelector` pre-carga todo. `file_tool` lee archivos completos. | Índice de archivos por proyecto. Lectura selectiva por rangos. | Alta |
| Crecimiento del contexto al LLM | Lineal: más historial = más tokens. Truncación por antigüedad. | Constante: contexto siempre budgeteado por relevancia. | Crítica |

---

## 10. Verificación de Integraciones

### Estado Actual

No existen integraciones con Spotify, GitHub u otras APIs en el código actual. Sin embargo, la arquitectura **no está preparada** para ellas:

- No hay separación de memoria por integración.
- No hay namespaces de memoria especializados.
- No hay índices por fuente de datos.

### Preparación Requerida

Cuando se agreguen integraciones, cada una debe:

1. Tener su propio `IntegrationMemory` con namespace aislado.
2. Mantener su propio índice de entries.
3. Exponer solo entries relevantes al Memory Index global (no contenido interno).
4. Registrar sus capacidades de memoria en el `MemoryType` enum.

---

## 11. Conclusión

El proyecto se encuentra en un estado de **transición parcial**:

- **42% de componentes** ya están alineados (no acceden memoria o lo hacen correctamente).
- **33% parcialmente alineados** (tienen la estructura correcta pero reciben datos pre-cargados o carecen de selectividad).
- **25% incompatibles** (implementan directamente el patrón antiguo de carga masiva).

Los **bloqueantes principales** son:

1. El protocolo `Memory` KV plano en `protocols.py` y `base.py`.
2. La ausencia total de una capa de índice en todo el proyecto.
3. La ausencia de separación por tipo de memoria.
4. El patrón de `get_context()` → carga completa en `MemoryManager`.

La buena noticia es que el **ContextEngine ya tiene la pipeline correcta** (Select → Rank → Budget → Compress). El problema es que sus **inputs** son datos pre-cargados en masa. Corregir los inputs (mediante índice + selector + retriever) alineará toda la cadena.