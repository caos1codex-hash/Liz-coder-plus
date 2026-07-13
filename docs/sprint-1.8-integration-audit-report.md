# Informe de Auditoría de Integración — Sprint 1.8

**Proyecto:** Liz Coder Plus  
**Fecha:** Auditoría de código completo  
**Alcance:** Todos los módulos Python en `packages/`, `apps/backend/src/` y `tests/`  
**Clasificación de severidad:** HIGH (bloqueante/riesgo de datos), MEDIUM (calidad/mantenibilidad), LOW (estilo/convención)

---

## Resumen Ejecutivo

Se auditaron **67 archivos Python** distribuidos en 6 paquetes. Se identificaron **23 hallazgos** agrupados en 10 categorías. Los hallazgos más críticos son:

1. **Duplicación de `EventBus`** entre core y backend con APIs inconsistentes.
2. **`ToolExecutor._active_tasks` indexado por `tool_name`** en lugar de un ID de ejecución único, lo que impide rastrear múltiples ejecuciones concurrentes de la misma herramienta.
3. **`stage_check_permissions` es un no-op** en el pipeline de ejecución.
4. **Ausencia de scheduler** a pesar de tener `TaskPriority` y `next_ready()`.
5. **Sin persistencia** para `TaskManager` ni `WorkflowManager` — todo se pierde al reiniciar.
6. **Aislamiento deficiente en tests** con contaminación de `sys.path` y `sys.modules`.

---

## 1. Dependencias Circulares

### Hallazgo 1.1 — Sin dependencias circulares reales

**Severidad:** LOW  
**Estado:** ✅ Sin problemas

El código maneja correctamente la dependencia potencialmente circular entre `pipeline.py` y `orchestrator.py` mediante `TYPE_CHECKING`:

- `pipeline.py` línea 47: `if TYPE_CHECKING: from src.orchestrator import Orchestrator`
- `orchestrator.py` línea 31: `from src.pipeline import ExecutionPipeline` (import real en runtime)

No se detectaron ciclos de importación reales en ningún otro par de módulos.

---

## 2. Duplicación de Funcionalidad

### Hallazgo 2.1 — `EventBus` duplicado con API inconsistente

**Severidad:** HIGH  
**Archivos afectados:**
- `packages/core/src/event_bus.py` (líneas 25–87)
- `apps/backend/src/events/bus.py` (líneas 19–42)

**Descripción:** Existen dos implementaciones de `EventBus` con firmas diferentes:

| Aspecto | Core (`event_bus.py`) | Backend (`events/bus.py`) |
|---|---|---|
| `publish()` retorna | `int` (número de handlers invocados) | `None` |
| `unsubscribe()` | Sí | No implementado |
| `subscribers()` | Sí | No |
| `event_types()` | Sí | No |
| `clear()` | Sí | No |

El backend utiliza el `EventBus` de core a través del shim `liz_core.py`, por lo que el `EventBus` de `apps/backend/src/events/bus.py` es **código muerto**.

**Recomendación:** Eliminar `apps/backend/src/events/bus.py` y importar desde core.

---

### Hallazgo 2.2 — `PermissionMode` duplicado con valores inconsistentes

**Severidad:** HIGH  
**Archivos afectados:**
- `packages/shared/src/enums.py` líneas 8–16: valores `"Confirmation"` / `"Automatic"` (Capitalized)
- `apps/backend/src/permissions/modes.py` líneas 19–27: valores `"Confirmation"` / `"Automatic"` (Capitalized)
- `packages/core/src/pipeline.py` línea 144: usa `"confirmation"` / `"automatic"` (lowercase)

**Descripción:** El enum existe en dos lugares con los mismos valores, pero el pipeline valida contra `{"confirmation", "automatic"}` (minúsculas) mientras el enum usa `"Confirmation"` / `"Automatic"`. La validación en el pipeline (línea 144–150) nunca coincidirá con los valores del enum del backend.

**Recomendación:** Unificar en un solo enum en `packages/shared/src/enums.py` y usarlo en todos los módulos. Normalizar a minúsculas.

---

### Hallazgo 2.3 — `ToolExecutionResult` duplicado

**Severidad:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/tool_executor.py` líneas 47–108: clase `ToolExecutionResult` con `__slots__`
- `packages/shared/src/models.py` líneas 51–58: modelo Pydantic `ToolExecutionResult`

**Descripción:** Dos clases con el mismo nombre pero diferente naturaleza (dataclass con `__slots__` vs modelo Pydantic). El core usa su propia versión internamente; la versión Pydantic en shared no es importada por ningún módulo activo.

**Recomendación:** Decidir cuál es la canónica. Si se usa Pydantic para la API, exponer el `to_dict()` del core como compatible.

---

### Hallazgo 2.4 — `_emit()` helper duplicado en 6+ módulos

**Severidad:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/orchestrator.py` líneas 307–312
- `packages/core/src/tool_executor.py` líneas 390–396
- `packages/core/src/task.py` líneas 538–545
- `packages/core/src/workflow.py` líneas 789–795
- `packages/core/src/planner.py` líneas 386–392
- `packages/core/src/observability.py` (método interno similar)

**Descripción:** El patrón `async def _emit(self, event_type, payload): if self._bus: try: await self._bus.publish(...)` está copiado literalmente en al menos 6 clases. Cualquier cambio en la lógica de emisión (ej. añadir logging, métricas) requiere actualizar 6 lugares.

**Recomendación:** Extraer a un mixin o función utilitaria `emit_safe(bus, event_type, payload)`.

---

### Hallazgo 2.5 — `ChatTurn` (core) vs `ChatMessage` (shared) vs `ConversationMessage` (memory)

**Severidad:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/session_manager.py` líneas 32–46: `ChatTurn` (dataclass con `slots=True`)
- `packages/shared/src/models.py` líneas 18–26: `ChatMessage` (Pydantic BaseModel)
- `packages/memory/src/memory/models/conversation.py` líneas 23–51: `ConversationMessage` (dataclass con `slots=True`)

**Descripción:** Tres representaciones diferentes de un mensaje de conversación. Cada una con campos ligeramente distintos (`timestamp` como `datetime` vs `str`, `role` como `str` vs `AgentRole` enum, etc.).

**Recomendación:** Definir un modelo canónico en `shared` y mapear desde/hacia los formatos específicos de cada capa.

---

### Hallazgo 2.6 — `Memory` Protocol (core) vs `MemoryBackend` Protocol (memory)

**Severidity:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/protocols.py` líneas 24–29: `Memory` Protocol (`store`, `retrieve`)
- `packages/memory/src/base.py` líneas 12–32: `MemoryBackend` Protocol (`initialize`, `store`, `retrieve`, `delete`, `list_keys`, `close`)

**Descripción:** Dos protocolos de memoria con interfaces diferentes. El `Memory` de core es más simple (2 métodos), mientras `MemoryBackend` de memory es más completo (6 métodos). Ninguno es usado consistentemente: `MemoryManager` no implementa ninguno de los dos protocolos.

**Recomendación:** Consolidar en un solo protocolo en `shared/`.

---

## 3. Acoplamiento Excesivo

### Hallazgo 3.1 — Pipeline accede a atributos privados del Orchestrator

**Severidad:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/pipeline.py` línea 199: `orch._memory is not None and orch._memory.is_initialized`
- `packages/core/src/pipeline.py` línea 187: `orch._build_context(req.session_id)` (método privado)
- `packages/core/src/pipeline.py` línea 273: `orch.agent_timeout` (acceso a propiedad, aceptable)

**Descripción:** Los stages del pipeline acceden a `orch._memory` (atributo privado con guion bajo) en lugar de usar una API pública. Esto acopla el pipeline a la estructura interna del orchestrator.

**Recomendación:** Exponer `orchestrator.has_memory` y `orchestrator.memory` como propiedades públicas, o pasar el contexto necesario a través del `PipelineRequest`.

---

### Hallazgo 3.2 — `liz_core.py` manipula `sys.path` y `sys.modules`

**Severidad:** HIGH  
**Archivo afectado:** `packages/core/liz_core.py` líneas 77–120

**Descripción:** El shim `liz_core.py` realiza manipulación peligrosa de `sys.path` y `sys.modules` en tiempo de importación:
- Línea 79–84: Elimina temporalmente todos los módulos `src.*` de `sys.modules`
- Línea 87: Inserta rutas temporales en `sys.path`
- Línea 97–106: Restaura el estado previo

Este patrón es frágil, no es thread-safe, y puede causar fallos intermitentes si el import ocurre concurrentemente.

**Recomendación:** Migrar a una estructura de paquete Python estándar con `pyproject.toml` e installs editables (`pip install -e .`).

---

### Hallazgo 3.3 — `ws_routes.py` crea singletons globales en tiempo de importación

**Severidad:** MEDIUM  
**Archivo afectado:** `apps/backend/src/api/ws_routes.py` líneas 144–145

```python
_global_ws_manager = WebSocketManager()
_global_orchestrator = Orchestrator(ws_manager=_global_ws_manager)
```

**Descripción:** Los singletons se crean cuando el módulo se importa, lo que significa que cada import (incluyendo tests) crea instancias reales. Los tests tienen `build_ws_router()` como alternativa, pero los singletons globales permanecen activos y pueden causar interferencia.

**Recomendación:** Usar un patrón de fábrica o inyección de dependencias para los singletons.

---

## 4. Rutas/APIs Duplicadas

### Hallazgo 4.1 — Sin rutas duplicadas

**Severidad:** —  
**Estado:** ✅ Sin problemas

Los endpoints existentes son únicos:
- `GET /health` — en `apps/backend/src/api/main.py` línea 118
- `GET /status` — en `apps/backend/src/api/main.py` línea 128
- `WS /ws/chat` — en `apps/backend/src/api/ws_routes.py` línea 158

No se encontraron rutas duplicadas.

---

## 5. APIs Inconsistentes

### Hallazgo 5.1 — `EventBus.publish()` retorna tipos diferentes

**Severidad:** MEDIUM  
**Archivos afectados:**
- `packages/core/src/event_bus.py` línea 57: `async def publish(...) -> int`
- `apps/backend/src/events/bus.py` línea 30: `async def publish(...) -> None`

**Descripción:** La misma operación retorna `int` en core (número de handlers) y `None` en backend. Cualquier código que dependa del valor de retorno se romperá al usar la versión incorrecta.

**Recomendación:** Unificar para que ambas retornen `int`.

---

### Hallazgo 5.2 — Modos de permiso inconsistentes (mayúsculas vs minúsculas)

**Severidad:** HIGH  
**Archivos afectados:**
- `apps/backend/src/permissions/modes.py` línea 26–27: `"Confirmation"` / `"Automatic"`
- `apps/backend/src/permissions/service.py` línea 162: compara contra `PermissionMode.AUTOMATIC` (que vale `"Automatic"`)
- `packages/core/src/pipeline.py` línea 144: `valid_modes = {"confirmation", "automatic"}` (minúsculas)
- `packages/shared/src/ws_models.py` línea 33: `Literal["confirmation", "automatic"]` (minúsculas)

**Descripción:** El pipeline valida modos en minúsculas, pero el enum del backend usa Capitalized. Un cliente enviando `mode="Confirmation"` (según el enum) sería silenciosamente convertido a `"confirmation"` por el pipeline (línea 150). Esto funciona accidentalmente pero es confuso y frágil.

**Recomendación:** Normalizar todo a minúsculas en el enum base.

---

### Hallazgo 5.3 — `datetime.utcnow()` deprecado vs `datetime.now(timezone.utc)`

**Severidad:** LOW  
**Archivos afectados:**
- `packages/shared/src/models.py` línea 25: `datetime.utcnow` (deprecado en Python 3.12+)
- Todos los demás módulos usan `datetime.now(timezone.utc)` correctamente

**Recomendación:** Reemplazar `datetime.utcnow` por `datetime.now(timezone.utc)`.

---

### Hallazgo 5.4 — Métodos sync/async mezclados en `SessionManager`

**Severidity:** LOW  
**Archivo afectado:** `packages/core/src/session_manager.py`

| Método | Tipo | Línea |
|---|---|---|
| `create_session()` | sync | 165 |
| `get_or_create()` | sync | 184 |
| `get()` | sync | 190 |
| `list_sessions()` | sync | 216 |
| `append_turn()` | async | 229 |
| `drop()` | async | 194 |
| `restore_session()` | async | 116 |

**Descripción:** La mezcla sync/async es intencional (las operaciones que tocan memoria son async), pero puede confundir a consumidores que esperan una API uniforme.

**Recomendación:** Documentar explícitamente cuáles métodos son async y por qué.

---

## 6. Scheduler Ausente

### Hallazgo 6.1 — No existe módulo de scheduler

**Severidad:** HIGH  
**Estado:** ✅ Confirmado

**Descripción:** Se confirmó que **no existe ningún módulo de scheduler** en el codebase. Sin embargo, el sistema tiene infraestructura preparada para uno:

- `TaskPriority` (`packages/core/src/task.py` líneas 71–89): define niveles CRITICAL, HIGH, NORMAL, LOW con pesos.
- `TaskManager.next_ready()` (`packages/core/src/task.py` líneas 508–519): retorna la tarea READY de mayor prioridad.
- `WorkflowManager` ejecuta tareas en orden de prioridad, pero **no hay un loop que llame periódicamente a `next_ready()`**.

El `Orchestrator` tiene un campo `_planner` (línea 85) pero no un campo `_scheduler`. Los planes se generan pero nunca se ejecutan automáticamente como tareas independientes.

**Recomendación:** Implementar un `Scheduler` que periódicamente llame a `next_ready()` y ejecute las tareas pendientes, idealmente como un `asyncio.Task` de fondo.

---

## 7. Persistencia Ausente para Tareas/Workflows

### Hallazgo 7.1 — `TaskManager` usa solo almacenamiento en memoria

**Severidad:** HIGH  
**Archivo afectado:** `packages/core/src/task.py` líneas 260–262

```python
self._tasks: dict[str, Task] = {}
self._history: list[Task] = []
```

**Descripción:** Todas las tareas se almacenan en un `dict` en memoria. Al reiniciar el proceso:
- Todas las tareas activas se pierden
- El historial (`_history`) se pierde (limitado a 200 entradas)
- Los estados WAITING/READY nunca se recuperan
- Los workflows en progreso se abandonan

Esto es particularmente problemático porque `WorkflowManager` (que también usa `dict` en memoria — `packages/core/src/workflow.py`) confía en `TaskManager` para rastrear el estado de las tareas.

**Recomendación:** Añadir una capa de persistencia SQLite (similar a `packages/memory/src/memory/database.py`) para `TaskManager` y `WorkflowManager`.

---

### Hallazgo 7.2 — `WorkflowManager` usa solo almacenamiento en memoria

**Severidad:** HIGH  
**Archivo afectado:** `packages/core/src/workflow.py` (aproximadamente línea 510)

```python
self._workflows: dict[str, Workflow] = {}
```

**Descripción:** Los workflows registrados se almacenan únicamente en un diccionario. No hay recuperación ante fallos ni reinicios.

**Recomendación:** Persistir workflow definitions y estado en SQLite.

---

## 8. `ToolExecutor._active_tasks` Indexado por Nombre

### Hallazgo 8.1 — `_active_tasks` usa `tool_name` como clave

**Severidad:** HIGH  
**Archivo afectado:** `packages/core/src/tool_executor.py` líneas 141, 231, 353, 364

```python
# Línea 141 - Definición
self._active_tasks: dict[str, asyncio.Task[Any]] = {}

# Línea 231 - Inserción
self._active_tasks[tool_name] = task

# Línea 353 - Eliminación
self._active_tasks.pop(tool_name, None)

# Línea 364 - Búsqueda para cancel
task = self._active_tasks.get(tool_name)
```

**Descripción:** El diccionario `_active_tasks` usa `tool_name` como clave. Esto significa:

1. **Solo una ejecución por nombre de herramienta:** Si la misma herramienta se ejecuta concurrentemente (ej. dos archivos simultáneamente con `FileTool`), la segunda ejecución sobrescribe la referencia de la primera en el diccionario. La primera tarea ya no se puede cancelar.
2. **`cancel(tool_name)` cancela la ejecución más reciente**, no una específica.
3. **`active_count` puede ser incorrecto** si hay ejecuciones sobrescritas.

**Recomendación:** Usar un ID de ejecución único (ej. `str(uuid.uuid4())`) como clave, y exponerlo en `ToolExecutionResult` para que el llamador pueda cancelar una ejecución específica.

---

## 9. `stage_check_permissions` es No-Op

### Hallazgo 9.1 — La etapa de permisos no hace nada

**Severidad:** HIGH  
**Archivo afectado:** `packages/core/src/pipeline.py` líneas 250–260

```python
async def stage_check_permissions(
    orch: "Orchestrator", req: PipelineRequest
) -> PipelineRequest:
    """Permission gate.

    Currently a no-op for agent invocation (agents are trusted).
    In the future this stage will check whether the selected agent
    is allowed to invoke its declared tools under the current mode.
    """
    # Reserved for future enforcement.
    return req
```

**Descripción:** La etapa está en `DEFAULT_STAGES` (línea 344) y se ejecuta en **cada mensaje**, consumiendo tiempo de medición (registrado en `stage_timings`) pero sin realizar ninguna validación. El `PermissionService` del backend (`apps/backend/src/permissions/service.py`) nunca es invocado desde el pipeline.

El sistema tiene `PermissionService` fully implementado pero desconectado del pipeline de ejecución. La verificación de permisos solo funciona para `ToolExecutor.execute()` cuando se le pasa un `permission_service`, pero el pipeline no conecta estos dos componentes.

**Recomendación:** 
1. Conectar el `PermissionService` al pipeline.
2. O si la etapa no se usa, eliminarla de `DEFAULT_STAGES` para evitar overhead innecesario.
3. Si se mantiene como placeholder, al menos documentar por qué es necesaria.

---

## 10. Aislamiento de Tests

### Hallazgo 10.1 — `sys.path` manipulado a nivel de módulo, no en fixtures

**Severidad:** HIGH  
**Archivos afectados:** Todos los archivos en `tests/unit/` y `tests/integration/`

**Descripción:** Cada archivo de test inserta rutas en `sys.path` a nivel de módulo:

```python
# Patrón repetido en ~20 archivos de test:
ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = ROOT / "packages" / "core"
sys.path.insert(0, str(CORE_SRC))
```

Problemas:
1. **`sys.path` se contamina entre tests:** Las inserciones son acumulativas y nunca se revierten.
2. **No se usa `conftest.py` para esto:** El helper `clear_src_cache()` existe en `tests/conftest.py` (línea 24) pero la mayoría de archivos lo reimplementan inline en lugar de usar un fixture.
3. **Orden de ejecución importa:** Si `test_event_bus.py` se ejecuta antes que `test_orchestrator.py`, el `sys.modules` ya contiene los módulos de core.

**Recomendación:** Crear un `autouse` fixture en `conftest.py` que gestione `sys.path` y `sys.modules` de forma automática.

---

### Hallazgo 10.2 — Singletons del backend no se limpian entre tests

**Severidad:** MEDIUM  
**Archivos afectados:**
- `apps/backend/src/api/ws_routes.py` líneas 144–145 (singletons globales)
- `tests/unit/test_ws_manager.py` (usa `build_ws_router()` para evitar el problema)
- Otros tests que importen de `ws_routes` indirectamente

**Descripción:** Los singletons `_global_ws_manager` y `_global_orchestrator` se crean en tiempo de importación y comparten estado entre tests. Aunque `build_ws_router()` existe, cualquier test que importe `ws_routes` dispara la creación de los singletons.

**Recomendación:** Usar fixtures de pytest con `scope="function"` para crear instancias frescas.

---

## 11. Código Muerto y Problemas Adicionales

### Hallazgo 11.1 — `apps/backend/src/core/orchestrator.py` es código muerto

**Severidad:** MEDIUM  
**Archivo afectado:** `apps/backend/src/core/orchestrator.py` (completo, 49 líneas)

**Descripción:** Este archivo es un stub del Sprint 1 con un `Orchestrator` que retorna `"not_implemented"`. El backend real usa el `Orchestrator` de `packages/core/src/orchestrator.py` vía el shim `liz_core.py`. El stub nunca se importa en producción.

**Recomendación:** Eliminar o marcar claramente como obsoleto.

---

### Hallazgo 11.2 — `packages/memory/src/sqlite.py` es un stub no funcional

**Severidad:** MEDIUM  
**Archivo afectado:** `packages/memory/src/sqlite.py` (completo, 57 líneas)

**Descripción:** Todos los métodos de `SQLiteMemoryBackend` son stubs que solo loguean. La implementación real de persistencia está en `packages/memory/src/memory/database.py` + `packages/memory/src/memory/manager.py`. El archivo `sqlite.py` es el diseño original del Sprint 1 que fue reemplazado.

**Recomendación:** Eliminar `sqlite.py` y actualizar cualquier referencia.

---

### Hallazgo 11.3 — `asyncio.ensure_future` fire-and-forget en SessionManager

**Severidad:** MEDIUM  
**Archivo afectado:** `packages/core/src/session_manager.py` línea 274

```python
asyncio.ensure_future(
    self._bus.publish(
        MEMORY_STORED,
        {"session_id": session_id, "turn": turn.__dict__},
    )
)
```

**Descripción:** Se usa `ensure_future` sin guardar la referencia ni `await`-earlo. Esto es un patrón fire-and-forget que:
1. Puede generar advertencias de "Task was destroyed but it is pending"
2. Si la publicación falla, la excepción se pierde (el `except RuntimeError` de la línea 280 solo captura el caso de no haber loop)
3. No hay garantía de que el evento se publique antes de que la función retorne

**Recomendación:** Usar `await self._bus.publish(...)` directamente (el método ya es async).

---

### Hallazgo 11.4 — Versiones inconsistentes entre paquetes

**Severidad:** LOW  
**Archivos afectados:**

| Paquete | Archivo | Versión |
|---|---|---|
| core | `packages/core/src/__init__.py` | 0.1.3 |
| backend | `apps/backend/src/__init__.py` | 0.1.2 |
| shared models | `packages/shared/src/models.py:74` | 0.1.1 |
| tools | `packages/tools/src/__init__.py` | 0.2.0 |
| memory | `packages/memory/src/__init__.py` | 0.1.2 |
| backend config | `apps/backend/src/core/config.py:73` | 0.1.2 |

**Recomendación:** Centralizar la versión en un único lugar (ej. `VERSION` en la raíz del repo).

---

### Hallazgo 11.5 — Ausencia de `__all__` en la mayoría de módulos

**Severidad:** LOW  
**Archivos afectados:**
- `packages/core/src/orchestrator.py` — sin `__all__`
- `packages/core/src/tool_executor.py` — sin `__all__`
- `packages/core/src/session_manager.py` — sin `__all__`
- `packages/core/src/agent_router.py` — sin `__all__`
- `packages/core/src/ws_manager.py` — sin `__all__`
- `packages/core/src/ws_connection.py` — sin `__all__`
- `packages/core/src/event_bus.py` — sin `__all__`
- `packages/core/src/events.py` — sin `__all__`
- `packages/core/src/protocols.py` — sin `__all__`
- `packages/agents/src/base.py` — sin `__all__`
- `packages/agents/src/registry.py` — sin `__all__`
- `packages/tools/src/base.py` — sin `__all__`
- `packages/tools/src/registry.py` — sin `__all__`
- `apps/backend/src/permissions/service.py` — sin `__all__`
- `apps/backend/src/permissions/modes.py` — sin `__all__`

**Módulos que SÍ tienen `__all__`:**
- `packages/core/src/pipeline.py`, `task.py`, `workflow.py`, `planner.py`, `observability.py`
- `packages/tools/src/__init__.py`
- `packages/memory/src/memory/__init__.py`, `models/__init__.py`, `repositories/__init__.py`

**Recomendación:** Añadir `__all__` a todos los módulos para clarificar la API pública y evitar importar símbolos internos accidentalmente.

---

### Hallazgo 11.6 — Rutas hardcodeadas relativas a `__file__`

**Severidad:** LOW  
**Archivos afectados:**
- `packages/core/liz_core.py` línea 25: `Path(__file__).resolve().parent / "src"`
- `apps/backend/src/api/ws_routes.py` líneas 26–27: `Path(__file__).resolve().parents[4]`
- `apps/backend/src/api/main.py` líneas 41–42: `Path(__file__).resolve().parents[4]`
- `apps/backend/src/core/config.py` líneas 84–86: `here.parents[3] / "config"`
- `packages/memory/src/memory/database.py` línea 149: `Path(__file__).parent / "migrations"`

**Descripción:** Varias rutas se calculan relativas a la ubicación del archivo, asumiendo una estructura de directorios fija. Si los archivos se mueven, las rutas se rompen silenciosamente.

**Recomendación:** Centralizar las rutas del proyecto en una variable de configuración o usar imports relativos del paquete.

---

### Hallazgo 11.7 — Sin comentarios TODO/FIXME

**Severidad:** —  
**Estado:** ✅ Limpio

No se encontraron comentarios `TODO`, `FIXME`, `HACK`, `XXX` ni `WORKAROUND` en ningún archivo Python.

---

## Tabla Resumen

| # | Categoría | Severidad | Hallazgo | Archivos Clave |
|---|---|---|---|---|
| 2.1 | Duplicación | HIGH | `EventBus` duplicado con API distinta | `core/event_bus.py`, `backend/events/bus.py` |
| 2.2 | Duplicación | HIGH | `PermissionMode` duplicado, valores inconsistentes | `shared/enums.py`, `backend/permissions/modes.py`, `core/pipeline.py` |
| 2.3 | Duplicación | MEDIUM | `ToolExecutionResult` duplicado | `core/tool_executor.py`, `shared/models.py` |
| 2.4 | Duplicación | MEDIUM | `_emit()` copiado en 6+ módulos | Múltiples en `packages/core/src/` |
| 2.5 | Duplicación | MEDIUM | 3 representaciones de mensaje de chat | `core/session_manager.py`, `shared/models.py`, `memory/models/conversation.py` |
| 2.6 | Duplicación | MEDIUM | 2 protocolos de memoria diferentes | `core/protocols.py`, `memory/base.py` |
| 3.1 | Acoplamiento | MEDIUM | Pipeline accede a privados del Orchestrator | `core/pipeline.py` |
| 3.2 | Acoplamiento | HIGH | `liz_core.py` manipula `sys.modules` | `core/liz_core.py` |
| 3.3 | Acoplamiento | MEDIUM | Singletons globales en ws_routes | `backend/api/ws_routes.py` |
| 5.1 | Inconsistencia | MEDIUM | `EventBus.publish()` retorna tipos distintos | `core/event_bus.py`, `backend/events/bus.py` |
| 5.2 | Inconsistencia | HIGH | Modos de permiso mayúsculas vs minúsculas | Múltiples módulos |
| 5.3 | Inconsistencia | LOW | `datetime.utcnow()` deprecado | `shared/models.py:25` |
| 5.4 | Inconsistencia | LOW | Métodos sync/async mezclados | `core/session_manager.py` |
| 6.1 | Scheduler | HIGH | No existe módulo de scheduler | N/A |
| 7.1 | Persistencia | HIGH | `TaskManager` solo en memoria | `core/task.py:261-262` |
| 7.2 | Persistencia | HIGH | `WorkflowManager` solo en memoria | `core/workflow.py` |
| 8.1 | Bug | HIGH | `_active_tasks` indexado por `tool_name` | `core/tool_executor.py:141,231,353` |
| 9.1 | No-Op | HIGH | `stage_check_permissions` no hace nada | `core/pipeline.py:250-260` |
| 10.1 | Tests | HIGH | `sys.path` contaminado entre tests | Todos los test files |
| 10.2 | Tests | MEDIUM | Singletons del backend sin limpiar | `backend/api/ws_routes.py` |
| 11.1 | Código Muerto | MEDIUM | Orchestrator stub del backend | `backend/core/orchestrator.py` |
| 11.2 | Código Muerto | MEDIUM | SQLiteMemoryBackend stub | `memory/src/sqlite.py` |
| 11.3 | Patrón | MEDIUM | `ensure_future` fire-and-forget | `core/session_manager.py:274` |
| 11.4 | Mantenibilidad | LOW | Versiones inconsistentes | 6 archivos |
| 11.5 | Convención | LOW | `__all__` ausente en ~15 módulos | Múltiples |
| 11.6 | Mantenibilidad | LOW | Rutas hardcodeadas relativas | 5 archivos |

---

## Prioridades Recomendadas para Sprint 1.8

### P0 — Bloqueantes para funcionalidad correcta
1. **Hallazgo 8.1** — Corregir `_active_tasks` para usar IDs de ejecución únicos.
2. **Hallazgo 5.2** — Normalizar modos de permiso a minúsculas.
3. **Hallazgo 9.1** — Conectar `PermissionService` al pipeline o eliminar la etapa.

### P1 — Riesgo de datos / estabilidad
4. **Hallazgo 7.1/7.2** — Añadir persistencia SQLite para `TaskManager` y `WorkflowManager`.
5. **Hallazgo 6.1** — Implementar `Scheduler` para ejecutar tareas pendientes.
6. **Hallazgo 10.1** — Mejorar aislamiento de tests con fixtures `autouse`.

### P2 — Deuda técnica
7. **Hallazgo 2.1** — Eliminar `EventBus` duplicado del backend.
8. **Hallazgo 2.4** — Extraer helper `_emit()` reutilizable.
9. **Hallazgo 3.2** — Migrar `liz_core.py` a imports estándar de paquete.
10. **Hallazgo 11.3** — Reemplazar `ensure_future` por `await`.
11. **Hallazgo 11.1/11.2** — Eliminar código muerto (stubs).

### P3 — Convención y calidad
12. **Hallazgo 11.5** — Añadir `__all__` a todos los módulos.
13. **Hallazgo 11.4** — Centralizar versión del proyecto.
14. **Hallazgo 5.3** — Reemplazar `datetime.utcnow()`.
15. **Hallazgo 2.3/2.5/2.6** — Consolidar modelos duplicados.

---

*Fin del informe de auditoría de integración — Sprint 1.8*