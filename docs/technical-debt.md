# Deuda Técnica — Liz Coder Plus

> **Fuente:** Informe de Auditoría de Integración Sprint 1.8 + Riesgos Sprint 1.7
> **Última actualización:** Sprint 1.9

---

## TD-001 — `EventBus` duplicado con API inconsistente

- **Descripción:** Existen dos implementaciones de `EventBus` con firmas diferentes. La versión del core tiene `publish() -> int`, `unsubscribe()`, `subscribers()`, `event_types()` y `clear()`. La del backend tiene `publish() -> None` y carece de los demás métodos. El backend usa la versión de core a través del shim `liz_core.py`, por lo que `apps/backend/src/events/bus.py` es código muerto.
- **Causa:** El archivo del backend fue creado como stub en Sprint 1 y nunca se eliminó cuando se conectó al core.
- **Archivos afectados:** `packages/core/src/event_bus.py`, `apps/backend/src/events/bus.py`
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-002 — `PermissionMode` duplicado con valores inconsistentes

- **Descripción:** Tres definiciones de `PermissionMode` con valores distintos. El enum del backend usa `"Confirmation"` (Capitalized), el enum de shared usa `"Confirmation"` (Capitalized), y el pipeline valida contra `"confirmation"` (minúsculas). Un cliente enviando `mode="Confirmation"` es silenciosamente convertido por el pipeline, lo cual funciona accidentalmente pero es frágil.
- **Causa:** Evolución independiente de los paquetes sin normalización centralizada.
- **Archivos afectados:** `packages/shared/src/enums.py`, `apps/backend/src/permissions/modes.py`, `apps/backend/src/core/config.py`, `packages/core/src/pipeline.py`
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-003 — `ToolExecutionResult` duplicado entre core y shared

- **Descripción:** Existe una clase `ToolExecutionResult` en `packages/core/src/tool_executor.py` y otra representación en `packages/shared/src/models.py`. Ambas representan el resultado de una ejecución de herramienta pero con campos diferentes.
- **Causa:** Desarrollo paralelo sin consolidación de modelos.
- **Archivos afectados:** `packages/core/src/tool_executor.py`, `packages/shared/src/models.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-004 — `_emit()` helper duplicado en 6+ módulos

- **Descripción:** El patrón `async def _emit(self, event_type, payload): if self._bus: try: await self._bus.publish(...)` está copiado literalmente en al menos 6 clases. Cualquier cambio en la lógica de emisión requiere actualizar 6 lugares.
- **Causa:** Refactor no realizado tras la iteración inicial.
- **Archivos afectados:** `packages/core/src/orchestrator.py`, `packages/core/src/tool_executor.py`, `packages/core/src/task.py`, `packages/core/src/workflow.py`, `packages/core/src/planner.py`, `packages/core/src/observability.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-005 — Tres representaciones de mensaje de chat

- **Descripción:** `ChatTurn` (core, dataclass con `slots=True`), `ChatMessage` (shared, Pydantic BaseModel) y `ConversationMessage` (memory, dataclass con `slots=True`). Cada una con campos ligeramente distintos.
- **Causa:** Cada paquete definió su propio modelo según sus necesidades.
- **Archivos afectados:** `packages/core/src/session_manager.py`, `packages/shared/src/models.py`, `packages/memory/src/memory/models/conversation.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-006 — Dos protocolos de memoria diferentes

- **Descripción:** `Memory` Protocol en core (2 métodos: `store`, `retrieve`) y `MemoryBackend` Protocol en memory (6 métodos: `initialize`, `store`, `retrieve`, `delete`, `list_keys`, `close`). Ninguno es usado consistentemente por `MemoryManager`.
- **Causa:** Diseño incremental sin consolidación.
- **Archivos afectados:** `packages/core/src/protocols.py`, `packages/memory/src/base.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-007 — Pipeline accede a atributos privados del Orchestrator

- **Descripción:** `pipeline.py` accede a `orch._memory`, `orch._memory.is_initialized` y `orch._build_context()`. Esto acopla el pipeline a detalles internos del orchestrator.
- **Causa:** El pipeline fue diseñado como parte interna del orchestrator sin una interfaz pública explícita.
- **Archivos afectados:** `packages/core/src/pipeline.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.2

---

## TD-008 — `liz_core.py` manipula `sys.path` y `sys.modules`

- **Descripción:** El shim `liz_core.py` elimina temporalmente módulos `src.*` de `sys.modules` e inserta rutas temporales en `sys.path`. Este patrón es frágil, no es thread-safe, y puede causar fallos intermitentes.
- **Causa:** Estructura de paquetes no estándar (sin `pyproject.toml` con installs editables).
- **Archivos afectados:** `packages/core/liz_core.py`
- **Prioridad:** ALTA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-009 — Singletons globales en `ws_routes.py`

- **Descripción:** Los singletons `_global_ws_manager` y `_global_orchestrator` se crean en tiempo de importación. Cada import (incluyendo tests) crea instancias reales que comparten estado.
- **Causa:** Patrón de inicialización simple que no escala.
- **Archivos afectados:** `apps/backend/src/api/ws_routes.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-010 — `EventBus.publish()` retorna tipos diferentes

- **Descripción:** La misma operación retorna `int` (número de handlers) en core y `None` en backend. (Resuelto al eliminar el EventBus duplicado del backend.)
- **Causa:** Implementaciones independientes sin especificación compartida.
- **Archivos afectados:** `packages/core/src/event_bus.py`, `apps/backend/src/events/bus.py`
- **Prioridad:** MEDIA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-011 — `datetime.utcnow()` deprecado

- **Descripción:** `packages/shared/src/models.py` usa `datetime.utcnow` (deprecado en Python 3.12+). El resto de módulos usan `datetime.now(timezone.utc)` correctamente.
- **Causa:** Código del Sprint 1.1 no actualizado.
- **Archivos afectados:** `packages/shared/src/models.py`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-012 — Métodos sync/async mezclados en SessionManager

- **Descripción:** La API de `SessionManager` mezcla métodos síncronos (`create_session`, `get`, `list_sessions`) con asíncronos (`append_turn`, `drop`, `restore_session`). La mezcla es intencional pero no está documentada.
- **Causa:** Las operaciones que tocan SQLite son async, las demás son sync.
- **Archivos afectados:** `packages/core/src/session_manager.py`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-013 — Ausencia de scheduler

- **Descripción:** A pesar de tener `TaskPriority` y `next_ready()`, no existía un loop que llamara periódicamente a `next_ready()` para ejecutar tareas pendientes automáticamente.
- **Causa:** El sistema de tareas del Sprint 1.7 fue diseñado sin ejecución automática.
- **Archivos afectados:** `packages/core/src/task.py`, `packages/core/src/scheduler.py` (nuevo en Sprint 1.8)
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-014 — `TaskManager` solo en memoria (sin persistencia)

- **Descripción:** `TaskManager` almacena tareas en un `dict` en memoria. Al reiniciar se pierden todos los estados (PENDING, READY, WAITING) y los workflows en progreso se abandonan.
- **Causa:** Sprint 1.7 no incluyó persistencia.
- **Archivos afectados:** `packages/core/src/task.py`
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-015 — `WorkflowManager` solo en memoria (sin persistencia)

- **Descripción:** `WorkflowManager` almacena workflows en un `dict` en memoria. Al reiniciar se pierden todos los workflows y sus estados.
- **Causa:** Sprint 1.7 no incluyó persistencia.
- **Archivos afectados:** `packages/core/src/workflow.py`
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-016 — `_active_tasks` indexado por `tool_name` en lugar de ID de ejecución

- **Descripción:** `ToolExecutor._active_tasks` usa `tool_name` como clave, lo que impide rastrear múltiples ejecuciones concurrentes de la misma herramienta. La segunda ejecución sobrescribe la primera, y `cancel(tool_name)` cancela la más reciente, no una específica.
- **Causa:** Diseño inicial simple que no consideró concurrencia por nombre de herramienta.
- **Archivos afectados:** `packages/core/src/tool_executor.py`
- **Prioridad:** ALTA
- **Estado:** resuelto
- **Sprint previsto:** 1.8

---

## TD-017 — `stage_check_permissions` es un no-op

- **Descripción:** La etapa de permisos en el pipeline se ejecuta en cada mensaje pero no realiza ninguna validación. El `PermissionService` del backend nunca es invocado desde el pipeline. Actualmente los agentes son confiados.
- **Causa:** La etapa fue reservada para Sprint 2 cuando existan agentes reales con declaraciones de herramientas.
- **Archivos afectados:** `packages/core/src/pipeline.py`
- **Prioridad:** ALTA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-018 — `sys.path` contaminado entre tests

- **Descripción:** Los archivos de tests insertan rutas en `sys.path` de forma acumulativa sin revertirlas. El helper `clear_src_cache()` en `conftest.py` existe pero la mayoría de archivos lo reimplementan inline.
- **Causa:** Estructura de paquetes no estándar que requiere `sys.path` manipulation.
- **Archivos afectados:** Todos los archivos de tests
- **Prioridad:** ALTA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-019 — Singletons del backend sin limpiar en tests

- **Descripción:** Los singletons `_global_ws_manager` y `_global_orchestrator` se crean al importar `ws_routes` y no se limpian entre tests.
- **Causa:** Patrón de singleton global en módulo.
- **Archivos afectados:** `apps/backend/src/api/ws_routes.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-020 — `apps/backend/src/core/orchestrator.py` es código muerto

- **Descripción:** El archivo es un stub que fue reemplazado por `packages/core/src/orchestrator.py`. El backend usa la versión de core a través del shim `liz_core.py`.
- **Causa:** Archivo original del Sprint 1 nunca eliminado.
- **Archivos afectados:** `apps/backend/src/core/orchestrator.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-021 — `packages/memory/src/sqlite.py` es código muerto (stub)

- **Descripción:** Todos los métodos de `SQLiteMemoryBackend` son stubs que solo loguean. La implementación real está en `packages/memory/src/memory/database.py` + `manager.py`.
- **Causa:** Diseño original del Sprint 1 reemplazado en Sprint 1.4.
- **Archivos afectados:** `packages/memory/src/sqlite.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-022 — `asyncio.ensure_future` fire-and-forget en SessionManager

- **Descripción:** En `packages/core/src/session_manager.py` línea 274, se usa `asyncio.ensure_future` para emitir un evento de forma fire-and-forget. Si la emisión falla, la excepción se pierde silenciosamente.
- **Causa:** Método sync que necesita emitir un evento async.
- **Archivos afectados:** `packages/core/src/session_manager.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-023 — Versiones inconsistentes entre paquetes

- **Descripción:** core=0.1.3, backend=0.1.2, shared=0.1.1, tools=0.2.0, memory=0.1.2, config=0.1.2. Seis archivos con números de versión diferentes.
- **Causa:** Cada paquete actualiza su versión independientemente.
- **Archivos afectados:** 6 archivos en `packages/` y `apps/`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-024 — Ausencia de `__all__` en ~15 módulos

- **Descripción:** La mayoría de módulos no definen `__all__`, lo que puede causar importaciones accidentales de símbolos internos.
- **Causa:** Convención no establecida al inicio del proyecto.
- **Archivos afectados:** ~15 módulos en `packages/` y `apps/`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.1

---

## TD-025 — Rutas hardcodeadas relativas a `__file__`

- **Descripción:** Varias rutas se calculan relativas a la ubicación del archivo (`Path(__file__).resolve().parents[N]`). Si los archivos se mueven, las rutas se rompen silenciosamente.
- **Causa:** Ausencia de una variable de configuración centralizada para rutas del proyecto.
- **Archivos afectados:** `packages/core/liz_core.py`, `apps/backend/src/api/ws_routes.py`, `apps/backend/src/api/main.py`, `apps/backend/src/core/config.py`, `packages/memory/src/memory/database.py`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-026 — `StreamingManager._get_stream_iterator` retorna async generator (no awaitable)

- **Descripción:** El método `_get_stream_iterator` retorna un generador asíncrono, pero los consumidores esperan un awaitable. La API es inconsistente: algunos callers hacen `await` (que falla con TypeError) mientras otros iteran correctamente. Se necesita una API unificada.
- **Causa:** Cambio tardío de `async def -> stream` a generador durante el desarrollo de Sprint 1.9.
- **Archivos afectados:** `packages/llm/src/llm/streaming/manager.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 1.9

---

## TD-027 — `ContextManager` usa estimación heurística de tokens (chars/4)

- **Descripción:** El `ContextManager` estima tokens con `len(text) / 4`, lo cual es impreciso para español y no refleja el tokenizado real del modelo. Puede causar que prompts excedan la ventana de contexto o que se recorte innecesariamente.
- **Causa:** Se optó por una solución ligera sin dependencia de tokenizers (tiktoken) en el MVP.
- **Archivos afectados:** `packages/llm/src/llm/context/manager.py`
- **Prioridad:** MEDIA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## TD-028 — Tests de providers requieren endpoints HTTP reales para integración

- **Descripción:** Las pruebas de integración de los proveedores LLM (Ollama, OpenAI, Anthropic, etc.) necesitan un endpoint HTTP real o un mock de servidor completo. Actualmente los tests unitarios usan mocks de `httpx` pero no existe un entorno de integración automatizado.
- **Causa:** Los providers se desarrollaron con mocks ligeros; no se configuró un servidor de prueba HTTP.
- **Archivos afectados:** `packages/llm/tests/`
- **Prioridad:** BAJA
- **Estado:** pendiente
- **Sprint previsto:** 2.0

---

## Resumen

| Prioridad | Total | Resueltos | Pendientes |
|---|---|---|---|
| ALTA | 8 | 5 | 3 |
| MEDIA | 12 | 2 | 10 |
| BAJA | 6 | 0 | 6 |
| **Total** | **26** | **7** | **19** |