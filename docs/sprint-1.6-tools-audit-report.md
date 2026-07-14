# Sprint 1.6 — Informe de Auditoría del Sistema de Herramientas

> Fecha: 2026-07-13
> Estado actual: 140 tests unitarios pasando, 1 test de integración roto (import error en `test_websocket_chat.py` — preexistente)

---

## 1. Alcance de la Auditoría

Se revisaron los siguientes componentes:

| Componente | Archivo | Paquete |
|---|---|---|
| BaseTool | `packages/tools/src/base.py` | tools |
| ToolRegistry | `packages/tools/src/registry.py` | tools |
| Tool Protocol | `packages/core/src/protocols.py` | core |
| ToolExecutor | `packages/core/src/tool_executor.py` | core |
| ToolExecutionResult | `packages/core/src/tool_executor.py` | core |
| PermissionService | `apps/backend/src/permissions/service.py` | backend |
| PermissionMode | `apps/backend/src/permissions/modes.py` | backend |
| PermissionsConfig | `apps/backend/src/core/config.py` | backend |
| Eventos de Tools | `packages/core/src/events.py` | core |
| Orchestrator (tools) | `packages/core/src/orchestrator.py` | core |
| Tests existentes | `tests/unit/test_tool_executor.py` | tests |
| Tests permisos | `tests/unit/test_permission_service.py` | tests |
| Tests modos | `tests/unit/test_permission_modes.py` | tests |
| Config | `config/development.json` | config |

---

## 2. Problemas Encontrados

### 2.1 CRÍTICO — Sin herramientas reales

**Severidad:** Alta
**Descripción:** No existe ninguna herramienta concreta (FileTool, TerminalTool, SystemTool). Solo hay `BaseTool` abstracto y `ToolRegistry` vacío. El sistema completo de tools es un esqueleto sin capacidad operativa real.

**Impacto:** Los agentes no pueden ejecutar ninguna acción del mundo real (leer archivos, ejecutar comandos, consultar el sistema).

---

### 2.2 CRÍTICO — BaseTool sin validación de entrada/salida

**Severidad:** Alta
**Descripción:** `BaseTool.execute()` es un método abstracto sin ninguna validación incorporada. Cada subclase debe implementar su propia validación, lo que genera inconsistencias y riesgos de seguridad. No hay:
- Validación de parámetros antes de la ejecución.
- Validación del resultado después de la ejecución.
- Esquema de parámetros declarativo.

**Impacto:** Herramientas mal implementadas pueden recibir parámetros inválidos o devolver resultados inesperados sin detección.

---

### 2.3 ALTO — BaseTool sin gestión de estado

**Severidad:** Alta
**Descripción:** A diferencia de `BaseAgent` que tiene estados de lifecycle (`CREATED → READY → RUNNING → ...`), `BaseTool` no tiene ningún estado. No se puede saber si una herramienta está disponible, ejecutándose, en error, etc.

**Impacto:** Imposible implementar lógica de reintento, monitoreo de herramientas ocupadas, o reporte de estado.

---

### 2.4 ALTO — BaseTool sin identificador único

**Severidad:** Media-Alta
**Descripción:** `BaseTool` tiene `name` (cadena configurada por el desarrollador) pero no un `id` único por instancia (UUID como tiene `BaseAgent`). Si se registran múltiples herramientas con el mismo `name`, el registro sobreescribe silenciosamente la anterior.

**Impacto:** Dificultad para auditoría, logging preciso y追踪 de ejecuciones individuales.

---

### 2.5 ALTO — ToolExecutor sin timeout

**Severidad:** Alta
**Descripción:** `ToolExecutor.execute()` no impone ningún límite de tiempo a la ejecución de herramientas. Si una herramienta se cuelga, el executor queda bloqueado indefinidamente. El Orchestrator tiene timeout para agentes pero el ToolExecutor no lo hereda ni lo usa.

**Impacto:** Una herramienta defectuosa puede bloquear todo el sistema.

---

### 2.6 ALTO — ToolExecutor sin cancelación

**Severidad:** Alta
**Descripción:** No existe mecanismo para cancelar una ejecución de herramienta en curso. No se usa `asyncio.Task` ni `asyncio.CancelledError`.

**Impacto:** Una vez que una herramienta empieza a ejecutarse, no hay forma de detenerla.

---

### 2.7 MEDIO — ToolRegistry no distingue herramientas internas/externas

**Severidad:** Media
**Descripción:** El registro es un dict plano. No hay concepto de:
- Herramientas internas vs externas vs plugins.
- Activación/desactivación de herramientas.
- Búsqueda por categoría.
- Metadata de registro (fecha de registro, autor, etc.).

**Impacto:** No se puede gestionar el ciclo de vida de herramientas individualmente ni preparar el terreno para un sistema de plugins.

---

### 2.8 MEDIO — PermissionService basado en strings, no en herramientas

**Severidad:** Media
**Descripción:** `PermissionService.evaluate(command)` recibe un string de comando, no una referencia a la herramienta. Esto significa:
- El sistema de permisos no conoce la categoría de la herramienta.
- No puede aplicar políticas basadas en metadata (LOW/MEDIUM/HIGH).
- No puede registrar qué agente solicitó la herramienta.

**Impacto:** El sistema de permisos es genérico pero no está integrado con la estructura de herramientas.

---

### 2.9 MEDIO — Sin niveles de permiso (LOW/MEDIUM/HIGH)

**Severidad:** Media
**Descripción:** El sprint 1.6 requiere tres niveles de permiso (LOW para lectura, MEDIUM para modificaciones, HIGH para ejecución). Actualmente solo existen dos modos (Confirmation/Automatic) y listas de comandos (allowed/denied). No hay un concepto de "nivel de riesgo" asociado a las herramientas.

**Impacto:** No se puede diferenciar entre "leer un archivo" (bajo riesgo) y "ejecutar un comando shell" (alto riesgo) a nivel de permisos.

---

### 2.10 MEDIO — Sin logging de acciones de herramientas

**Severidad:** Media
**Descripción:** No existe un registro persistente de qué herramientas se ejecutaron, quién las solicitó, cuándo y con qué resultado. Los eventos se emiten al EventBus pero no se persisten en ningún lado.

**Impacto:** Imposible realizar auditoría forense o análisis de uso de herramientas.

---

### 2.11 BAJO — ToolRegistry usa import circular potencial

**Severidad:** Baja
**Descripción:** `packages/tools/src/registry.py` importa `from src.protocols import Tool`, pero `protocols.py` está en `packages/core/src/`. El `sys.path` debe estar configurado correctamente para que esto funcione. Esto funciona en tests pero puede fallar en entornos de producción si el path no está configurado.

**Impacto:** Fragilidad en la importación cruzada entre paquetes.

---

### 2.12 BAJO — `__init__.py` del paquete tools es mínimo

**Severidad:** Baja
**Descripción:** `packages/tools/src/__init__.py` solo tiene `__version__`. No expone las clases principales (`BaseTool`, `ToolRegistry`, `ToolCategory`).

**Impacto:** Los consumidores deben conocer la estructura interna del paquete para importar.

---

### 2.13 BAJO — Herramientas no tienen acceso al contexto de sesión

**Severidad:** Baja-Media
**Descripción:** `BaseTool.execute(params)` solo recibe un dict de parámetros. No tiene acceso al `session_id`, `agent_name`, ni al `EventBus`. Esto limita la capacidad de las herramientas para emitir eventos propios o acceder al contexto de la conversación.

**Impacto:** Las herramientas son "cajas negras" sin conocimiento del contexto en el que operan.

---

### 2.14 INFO — Integración test roto

**Severidad:** Info
**Descripción:** `tests/integration/test_websocket_chat.py` falla al importar con `ModuleNotFoundError: No module named 'src.agent_router'`. Este error es preexistente (no causado por este sprint) y está relacionado con el shim `liz_core.py`.

**Impacto:** No se pueden ejecutar tests de integración completos. Los 140 tests unitarios pasan correctamente.

---

## 3. Riesgos

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| R1 | Herramientas sin timeout pueden bloquear el sistema | Alto | Implementar timeout configurable en ToolExecutor |
| R2 | Sin cancelación, herramientas en loop infinito son irrecuperables | Alto | Usar asyncio.Task + cancelación |
| R3 | Sin validación de entrada, herramientas pueden recibir datos corruptos | Medio | Agregar validate_input/validate_output a BaseTool |
| R4 | Sin niveles de permiso, no se puede aplicar política granular | Medio | Implementar PermissionLevel en BaseTool + PermissionService |
| R5 | Sin herramientas reales, el sistema es no funcional | Alto | Crear FileTool, SystemTool, TerminalTool en Phase 6 |
| R6 | El PermissionService no está en packages/tools, está en backend | Medio | Considerar mover o crear bridge |

---

## 4. Mejoras Necesarias

### 4.1 BaseTool (Phase 2)
- [ ] Agregar `id` único (UUID)
- [ ] Agregar estados de lifecycle (AVAILABLE, EXECUTING, COMPLETED, ERROR)
- [ ] Agregar `permission_level` (LOW, MEDIUM, HIGH)
- [ ] Agregar `parameters_schema` (declarativo)
- [ ] Agregar `validate_input(params)` con hook para subclases
- [ ] Agregar `validate_output(result)` con hook para subclases
- [ ] Exponer clases en `__init__.py`

### 4.2 ToolRegistry (Phase 3)
- [ ] Soportar fuente (internal/external/plugin)
- [ ] Activar/desactivar herramientas
- [ ] Búsqueda por categoría
- [ ] Prevenir sobreescribito silencioso (opción configurable)
- [ ] Listar herramientas por nivel de permiso

### 4.3 ToolExecutor (Phase 4)
- [ ] Timeout configurable por herramienta
- [ ] Cancelación vía asyncio.Task
- [ ] Pasar contexto (session_id, agent_name) a la herramienta
- [ ] Logging estructurado de cada ejecución
- [ ] Captura y formateo uniforme de errores
- [ ] Resultados estructurados estandarizados

### 4.4 PermissionService (Phase 5)
- [ ] Niveles LOW/MEDIUM/HIGH
- [ ] Evaluación basada en metadata de herramienta (no solo string)
- [ ] Registro de auditoría (tool, agent, timestamp, result)
- [ ] Modo confirmación: flujo completo con approve/deny callback
- [ ] Modo automático: respeto a niveles + whitelist

### 4.5 Herramientas Base (Phase 6)
- [ ] FileTool: leer, escribir, listar directorios
- [ ] SystemTool: info del sistema, estado
- [ ] TerminalTool: ejecutar comandos con permisos

### 4.6 Testing (Phase 7)
- [ ] Tests para BaseTool (creación, validación, estados)
- [ ] Tests para ToolRegistry (registro, búsqueda, duplicados, activación)
- [ ] Tests para ToolExecutor (timeout, cancelación, errores)
- [ ] Tests para PermissionService con niveles
- [ ] Tests para cada herramienta base

---

## 5. Deuda Técnica

| Ítem | Descripción | Prioridad |
|---|---|---|
| TD-1 | Paquete tools no expone API pública en `__init__.py` | Baja |
| TD-2 | PermissionService en `apps/backend` en lugar de paquete compartido | Media |
| TD-3 | Sin herramientas concretas desde Sprint 1 | Alta |
| TD-4 | ToolExecutor sin timeout ni cancelación | Alta |
| TD-5 | BaseTool sin lifecycle (a diferencia de BaseAgent) | Media |
| TD-6 | Herramientas no reciben contexto de ejecución | Media |
| TD-7 | Test de integración WebSocket roto (preexistente) | Baja |

---

## 6. Conclusión

El sistema de herramientas está bien estructurado como esqueleto pero carece de funcionalidad operativa. Los componentes base (`BaseTool`, `ToolRegistry`, `ToolExecutor`, `PermissionService`) existen y tienen buena arquitectura, pero necesitan mejoras significativas en validación, seguridad, lifecycle y capacidades reales. El sprint 1.6 debe transformar este esqueleto en un sistema funcional y seguro.

**Próximo paso:** Phase 2 — Mejorar la arquitectura de BaseTool.