# Arquitectura — Liz Coder Plus

> Visión general de la arquitectura del proyecto.
> Versión: `v0.2.1` — Sprint 1.9 (Motor LLM: Providers, Streaming, Contexto y Prompt).

## 1. Visión general

Liz Coder Plus es un asistente IA de escritorio para Windows. La
arquitectura sigue un patrón **cliente-servidor local**: una aplicación
desktop (WinUI 3) se comunica con un backend (FastAPI) que orquesta
agentes, memoria y herramientas. Toda la lógica de negocio vive en el
backend; el desktop es una capa fina de presentación.

```
┌─────────────────┐     WebSocket      ┌─────────────────┐
│   Desktop App   │ ◄───────────────► │     Backend     │
│   (WinUI 3)     │     REST + JSON   │   (FastAPI)     │
│      MVVM       │                   │                 │
└─────────────────┘                   └────────┬────────┘
                                               │
                                               ▼
                                      ┌────────────────┐
                                      │  Orchestrator  │
                                      │    (Core)      │
                                      └────────┬───────┘
                                               │
                       ┌───────────────┬───────┴────────┬───────────────┐
                       ▼               ▼                ▼               ▼
                 ┌──────────┐    ┌──────────┐     ┌──────────┐    ┌──────────┐
                 │  Agents  │    │  Memory  │     │  Tools   │    │  Shared  │
                 │          │    │ (SQLite) │     │          │    │  Types   │
                 └──────────┘    └──────────┘     └──────────┘    └──────────┘
```

## 2. Capas

### 2.1 Desktop (`apps/desktop`)

- **Tecnología:** WinUI 3 / .NET 8.
- **Patrón:** MVVM (CommunityToolkit.Mvvm).
- **Responsabilidad:** presentar la interfaz al usuario y reenviar
  entradas al backend por WebSocket.
- **Sin lógica de negocio.** Toda decisión se delega al backend.

### 2.2 Backend (`apps/backend`)

- **Tecnología:** Python 3.11+ / FastAPI / WebSocket.
- **Responsabilidad:** recibir mensajes, invocar el orquestador,
  gestionar permisos, exponer endpoints.
- **Endpoints previstos:**
  - `GET /health` — estado del servicio.
  - `GET /status` — información de versión y entorno.
  - `WS /ws` — canal bidireccional con el desktop.
  - `POST /chat` — envío de mensaje (alternativa a WebSocket).
  - `POST /tools/execute` — ejecución de herramientas.

### 2.3 Core (`packages/core`)

- **Orquestador:** punto central que recibe cada mensaje, decide el
  agente, coordina memoria y herramientas, y emite eventos. Soporta
  lifecycle (`initialize()` / `shutdown()`), timeout configurable
  por agente, y ejecución de herramientas con permisos.
- **EventBus:** bus pub/sub interno (`event_bus.py`) para desacoplar
  módulos. Soporta subscribe, unsubscribe, publish con retorno de
  contador, y fault isolation entre handlers.
- **Eventos:** el catálogo está en `packages/core/src/events.py`.
  Incluye ciclos de vida de usuario, agentes, herramientas, permisos
  y sistema.
- **AgentRouter:** decide qué agente atiende cada mensaje. Las reglas
  son predicados async evaluados en orden; la primera coincidencia
  gana. Valida el Protocol `Agent` al registrar. Incluye `EchoAgent`
  como fallback que usa el contexto (historial, modo, idioma).
- **ToolExecutor:** ejecuta herramientas con gating de permisos.
  Flujo: permission check → deny/confirm/allow → ejecución → evento.
- **Protocols:** contratos estructurales en `protocols.py` para
  `Agent`, `Memory` y `Tool` (duck-typing con `runtime_checkable`).
- **SessionManager:** almacena en memoria el historial temporal de
  cada conversación (hasta 200 turnos), el idioma preferido del usuario
  y el último modo de permiso solicitado. Integrado con memoria
  persistente vía `MemoryManager`.
- **WebSocketManager:** registro de conexiones activas. Soporta
  broadcast global, broadcast por sesión y envío dirigido.

El paquete `core` se expone al backend a través de un shim
`packages/core/liz_core.py` que evita colisiones de namespace `src`
entre el backend y el core.

### 2.4 Memory (`packages/memory`)

- **Backend funcional:** SQLite (vía aiosqlite, acceso asíncrono nativo).
- **Componentes:**
  - `MemoryManager`: API pública del sistema de memoria. Gestiona la
    inicialización, mantiene un cache RAM por sesión y delega al
    repositorio para persistencia.
  - `DatabaseManager`: gestiona la conexión SQLite, ejecuta migraciones
    automáticas y expone métodos de bajo nivel.
  - `ConversationRepository`: capa de acceso a datos con operaciones
    CRUD asíncronas sobre la tabla `conversations`.
  - `ConversationMessage`: modelo de datos (dataclass) para mensajes.
- **Flujo de persistencia:** cuando el `SessionManager` tiene un
  `MemoryManager` adjuntado, cada `append_turn()` persiste el mensaje
  tanto en la cache RAM como en SQLite. Al reiniciar, `restore_session()`
  recarga el historial desde la base de datos.
- **Configuración:** `config/development.json` → sección `memory`.
- **Contrato genérico (futuro):** el Protocol `MemoryBackend` de
  `packages/memory/src/base.py` está disponible para backends alternativos.

### 2.5 Agents (`packages/agents`)

- **BaseAgent:** clase abstracta con ciclo de vida completo.
  - **Estados:** `CREATED → READY → RUNNING → COMPLETED/FAILED → SHUTDOWN`.
  - **Métodos:** `initialize()`, `shutdown()`, `safe_handle()` (con
    aislamiento de errores y métricas), `handle()` (abstracto).
  - **Metadata:** `name`, `description`, `version`, `capabilities`,
    `agent_id`, `state`, `tasks_completed`, `tasks_failed`.
  - Los agentes que fallan pueden reintentar (estado `FAILED` permite
    nuevas ejecuciones).
- **AgentRegistry:** registro con validación del Protocol `Agent`.
- **Contrato dual:** los agentes pueden heredar de `BaseAgent` (con
  lifecycle) o simplemente satisfacer el Protocol `Agent` (duck-typing).
  El Orchestrator acepta ambos.

### 2.6 Tools (`packages/tools`)

- **BaseTool:** clase abstracta con lifecycle completo, validación y
  métricas (Sprint 1.6).
  - **Identidad:** `name`, `tool_id` (UUID), `description`, `version`.
  - **Categorías:** `SHELL`, `FILE`, `WEB`, `APP`, `SYSTEM`, `CUSTOM`
    (enum `ToolCategory`).
  - **Nivel de permiso:** `LOW`, `MEDIUM`, `HIGH` (enum `PermissionLevel`).
  - **Estados:** `AVAILABLE → EXECUTING → COMPLETED/ERROR`, más `DISABLED`.
  - **Validación:** `validate_input(params)` y `validate_output(result)`
    con soporte para `parameters_schema` declarativo.
  - **Métricas:** `executions`, `failures`, `last_error`, `total_time_ms`.
  - **Contrato:** subclases implementan `_execute(params, context) -> dict`;
    el método `execute()` de la base gestiona lifecycle, validación y métricas.
  - **Contexto de ejecución:** `execute(params, context={session_id, agent_name})`.
- **ToolRegistry:** registro mejorado con validación duck-typing.
  - Soporta fuentes: `INTERNAL`, `EXTERNAL`, `PLUGIN` (enum `ToolSource`).
  - Políticas de duplicados: `OVERWRITE`, `RAISE`, `SKIP` (enum `DuplicatePolicy`).
  - Activación/desactivación individual de herramientas.
  - Búsqueda por categoría, nivel de permiso y fuente.
  - Método `summary()` con estadísticas del registro.
- **Herramientas base (Sprint 1.6):**
  - `FileTool` — leer, escribir, listar archivos y directorios. MEDIUM.
  - `SystemTool` — información del sistema y estado. LOW.
  - `TerminalTool` — ejecutar comandos shell. HIGH.
- **Seguridad:** toda ejecución pasa por `ToolExecutor` que consulta
  `PermissionService` antes de ejecutar. Decisiones: `allow`,
  `confirm`, `deny`, `timeout`, `cancelled`.

### 2.7 Shared (`packages/shared`)

- **Contenido:** modelos Pydantic, enumeraciones, tipos y utilidades.
- **Regla:** este paquete **no depende de ningún otro** del proyecto.
  Puede ser importado por todos sin crear ciclos.

## 3. Flujo de un mensaje

1. El usuario escribe un mensaje en el Desktop (`MainWindow` → `ChatService`).
2. El `ChatService` abre (o reutiliza) una conexión WebSocket a `/ws/chat`
   y envía un `WebSocketChatRequest`:
   ```json
   { "message": "Hola Liz", "session_id": "uuid", "mode": "confirmation" }
   ```
3. El backend recibe el JSON, valida los campos y delega en el
   `WebSocketManager` para registrar/enrutar la conexión.
4. El `Orchestrator.handle_message()` se ejecuta:
   - Persiste el turno del usuario en el `SessionManager` (RAM + SQLite
     si hay un `MemoryManager` adjuntado).
   - Consulta el `AgentRouter` para seleccionar un agente.
   - Invoca al agente, que produce una respuesta de texto.
   - Persiste el turno del asistente (RAM + SQLite).
5. El orquestador devuelve la respuesta al endpoint WebSocket.
6. El endpoint emite uno o varios `WebSocketChatResponse` (chunks
   mientras se genera el texto, luego un sobre final con
   `status="completed"`).
7. El `ChatService` del Desktop recibe los sobres y los reenvía al
   hilo de UI, que los acumula en la burbuja del asistente.
8. Cuando llega el sobre final, la burbuja se cierra con el texto
   completo.

## 4. Decisiones de diseño

| Decisión                        | Justificación                                              |
|---------------------------------|------------------------------------------------------------|
| Monorepo                        | Facilita refactorizaciones entre módulos.                  |
| Cliente-servidor local          | Permite futuro despliegue multi-dispositivo.               |
| WebSocket bidireccional         | Conversación en tiempo real, sin polling.                  |
| MVVM en desktop                 | Separa UI de lógica y facilita pruebas.                    |
| Protocolos/ABC en Python        | Contratos explícitos entre módulos.                        |
| EventBus interno                | Desacopla módulos y habilita auditoría y monitoreo.       |
| Agent lifecycle (BaseAgent)      | Estados explícitos, métricas, recuperación.              |
| Timeout en agentes              | `asyncio.wait_for` con configurable por Orchestrator.     |
| Protocol validation             | `isinstance` en registro para prevenir errores runtime.  |
| Permisos en backend             | El desktop nunca ejecuta acciones peligrosas.              |
| Configuración por entorno       | Diferencia desarrollo / producción.                        |
| SQLite vía aiosqlite            | Acceso asíncrono nativo, sin overhead de SQLAlchemy.      |
| Cache RAM + persistencia       | Lecturas rápidas desde RAM, persistencia en SQLite.       |
| Tool lifecycle (BaseTool)       | Estados explícitos, validación, métricas por instancia.   |
| Permission levels (LOW/MED/HIGH) | Política granular basada en riesgo de la herramienta.   |
| Timeout y cancelación en tools   | `asyncio.wait_for` + `Task.cancel()` en ToolExecutor.       |

## 5. Evolución prevista

- **Sprint 2:** primer agente conversacional con LLM.
- **Sprint 3:** permisos en producción + plugins de herramientas.
- **Sprint 4:** UI desktop con chat.
- **Sprint 5:** integración real con modelos IA.
- **Sprint 6+:** voz, imágenes, control avanzado del PC.

## 6. Sistema de Memoria — Detalle Técnico

### 6.1 Componentes

El sistema de memoria se implementa en `packages/memory/src/memory/` y
consta de cuatro componentes con responsabilidades bien definidas:

```
MemoryManager (API pública)
    ├── add_message(session_id, role, content, metadata)
    ├── get_context(session_id, limit?) -> list[dict]
    ├── clear_session(session_id) -> bool
    └── close()
        │
        ▼
ConversationRepository (acceso a datos)
    ├── save_message(session_id, role, content, metadata) -> int
    ├── get_session_history(session_id, limit?) -> list[dict]
    ├── delete_session(session_id) -> bool
    └── count_messages(session_id) -> int
        │
        ▼
DatabaseManager (conexión SQLite)
    ├── initialize()
    ├── connection() -> Connection
    ├── execute(sql, params) -> Cursor
    ├── execute_script(sql)
    └── close()
        │
        ▼
SQLite (tabla conversations + índices)
```

### 6.2 Flujo de Datos Completo

```
Usuario
  │
  ▼  WebSocket JSON
Backend FastAPI (/ws/chat)
  │
  ▼  Validación con WebSocketChatRequest (Pydantic)
Orchestrator.handle_message()
  │
  ├─→ SessionManager.append_turn(role="user", ...)
  │       │
  │       ├─→ RAM cache (list[ChatTurn])
  │       └─→ MemoryManager.add_message() ──→ SQLite (si adjuntado)
  │
  ▼  AgentRouter.route(message, context)
Agente (EchoAgent / futuro LLM)
  │
  ▼  response_text
Orchestrator
  │
  ├─→ SessionManager.append_turn(role="assistant", ...)
  │       │
  │       ├─→ RAM cache
  │       └─→ MemoryManager.add_message() ──→ SQLite
  │
  ▼  Envelope dict {type, content, status, session_id}
WebSocket → Cliente Desktop
```

### 6.3 Inicialización en el Backend

Durante el `lifespan` de FastAPI (`apps/backend/src/api/main.py`),
se ejecuta `_initialize_memory()` que:

1. Lee `config/development.json` → sección `memory`.
2. Si `memory.enabled == true`, crea un `MemoryManager`.
3. Inicializa la base de datos SQLite en `memory.path`.
4. Llama a `orchestrator.attach_memory(mm)`, que también
   adjunta el memory al `SessionManager`.

Si la inicialización falla (por ejemplo, sin `aiosqlite`), el sistema
continúa funcionando con sesiones en RAM únicamente.

### 6.4 Validaciones Implementadas (Sprint 1.4)

| Componente | Validación |
|---|---|
| `MemoryManager.add_message()` | Revisa que role ∈ {user, assistant, system}; que content no sea vacío. |
| `ConversationRepository.save_message()` | Valida role contra `_VALID_ROLES`. |
| `ConversationRepository` | Verifica que `lastrowid` no sea None tras INSERT. |
| `DatabaseManager._run_migration()` | Captura `OSError` al leer archivos y errores SQL al ejecutar. |
| `Orchestrator.handle_message()` | Separa `ValueError/TypeError` (validación) de errores inesperados. |

### 6.5 Código Muerto Documentado

Los siguientes archivos existen como esqueletos para futuros sprints
y NO deben ser eliminados sin considerar el roadmap:

| Archivo | Estado | Sprint Previsto |
|---|---|---|
| `packages/memory/src/base.py` | Protocol genérico, sin consumidores aún | Sprint 3 |
| `packages/memory/src/models.py` | Modelos SQLAlchemy, reservados para migraciones complejas | Sprint 3 |
| `packages/memory/src/sqlite.py` | Implementación SQLAlchemy, reservada como alternativa | Sprint 3 |
| `packages/agents/` | Paquete completo, sin uso | Sprint 2 |
| `packages/tools/` | Paquete funcional con 3 herramientas base | Sprint 1.6 (activo) |
| `apps/backend/src/core/orchestrator.py` | Stub, reemplazado por `packages/core/` | Eliminar en Sprint 2 |
| `apps/backend/src/events/bus.py` | EventBus original, reemplazado por `packages/core/src/event_bus.py` | Eliminar en Sprint 2 |

Nota: `packages/agents/` y `packages/tools/` ya NO son código muerto —
sus clases base y registros son funcionales desde Sprint 1.5.

### 6.6 Cómo Extender el Sistema de Memoria

Para agregar un nuevo backend de almacenamiento (ej. PostgreSQL):

1. Crear una clase en `packages/memory/src/memory/` que implemente los
   mismos métodos que `ConversationRepository`.
2. Crear un `DatabaseManager` alternativo o extender el existente.
3. Actualizar `MemoryManager.initialize()` para soportar el nuevo
   proveedor según `config.memory.provider`.
4. El `SessionManager` no necesita cambios porque interactúa
   exclusivamente a través de `MemoryManager`.

## 7. Sistema de Agentes — Detalle Técnico (Sprint 1.5)

### 7.1 Tipos de Agentes

| Tipo | Descripción | Sprint |
|---|---|---|
| EchoAgent | Fallback que reconoce mensajes en español, usa contexto | Sprint 1 (activo) |
| Conversacional | Diálogo general con LLM | Sprint 2 |
| Código | Asistencia de programación | Sprint 2 |
| Sistema | Control del PC y herramientas | Sprint 3 |
| Investigación | Búsqueda y síntesis | Futuro |

### 7.2 Ciclo de Vida de un Agente

```
nuevo BaseAgent()
    │
    ▼  state = CREATED
agente sin uso; no puede procesar mensajes
    │
    ▼  agent.initialize()
state = READY
    │
    ▼  agent.safe_handle(msg, ctx)
state = RUNNING → COMPLETED (o FAILED)
    │
    ▼  (puede recibir más mensajes desde READY/COMPLETED/FAILED)
    │
    ▼  agent.shutdown()
state = SHUTDOWN
```

El Orchestrator gestiona el ciclo de vida de todos los agentes
registrados a través de `orchestrator.initialize()` y
`orchestrator.shutdown()`.

### 7.3 Flujo de Ejecución con Eventos

```
Usuario → WebSocket → Orchestrator
    │
    ├─→ emit: user.message.received
    ├─→ SessionManager.append_turn(role="user")
    ├─→ AgentRouter.route(message, context)
    ├─→ emit: agent.invoked
    ├─→ agent.handle(message, context)  [con timeout]
    ├─→ emit: agent.completed (o agent.failed)
    ├─→ SessionManager.append_turn(role="assistant")
    │
    ▼  Envelope {type, content, status, agent_name, duration_ms}
WebSocket → Cliente
```

### 7.4 Sistema de Permisos para Herramientas

```
Agente solicita herramienta
    │
    ▼  ToolExecutor.execute(tool, params, session_id, agent_name)
    │
    ├─→ emit: tool.requested (con agent_name, session_id, tool_id)
    ├─→ PermissionService.evaluate_tool(tool_name, level, agent_name)
    │
    ├─ decision == "deny" → emit: tool.denied → return
    ├─ decision == "confirm" → return (cliente debe aprobar)
    ├─ decision == "allow" → asyncio.wait_for(tool.execute(...), timeout)
    │       ├─ éxito → validate_output → emit: tool.executed → return
    │       ├─ timeout → cancel task → emit: tool.failed → return
    │       ├─ cancelled → emit: tool.failed → return
    │       └─ error → emit: tool.failed → return
    │
    ▼  ToolExecutionResult {
        tool_name, decision, success, output, error,
        duration_ms, agent_name, session_id, tool_id
    }
```

Niveles de permiso (Sprint 1.6):

| Nivel | Descripción | Ejemplos |
|---|---|---|
| LOW | Operaciones de lectura, información básica | SystemTool, FileTool.read_file |
| MEDIUM | Modificaciones controladas | FileTool.write_file |
| HIGH | Ejecución de comandos, acciones críticas | TerminalTool |

### 7.5 Cómo Crear un Nuevo Agente

1. Crear un archivo en `packages/agents/src/` (o importar desde otro
   paquete).
2. Heredar de `BaseAgent` o crear una clase que satisfaga el Protocol:

   ```python
   from src.base import BaseAgent

   class MiAgente(BaseAgent):
       name = "mi_agente"
       description = "Hace algo útil"
       capabilities = ["ejemplo"]

       async def handle(self, message: str, context: dict) -> str:
           return f"Procesado: {message}"
   ```

3. Registrar en el Orchestrator:

   ```python
   orch = Orchestrator()
   orch.register_agent(MiAgente())
   await orch.initialize()  # transiciona a READY
   ```

4. Opcionalmente agregar una regla de routing:

   ```python
   async def es_mi_agente(msg, ctx) -> bool:
       return msg.startswith("@")

   orch.router.add_rule("mi_agente", es_mi_agente)
   ```

### 7.6 Estados del Orchestrator

| Estado | Propiedad | Descripción |
|---|---|---|
| No inicializado | `is_initialized=False` | Agentes registrados pero no listos |
| Activo | `is_initialized=True, is_shutdown=False` | Listo para procesar mensajes |
| Apagado | `is_shutdown=True` | Agentes cerrados, no procesa mensajes |

## 8. Sistema de Herramientas — Detalle Técnico (Sprint 1.6)

### 8.1 Estructura del Paquete Tools

```
packages/tools/src/
├── __init__.py          → API pública (BaseTool, ToolRegistry, FileTool, ...)
├── base.py             → BaseTool, ToolCategory, ToolState, PermissionLevel, ToolError
├── registry.py         → ToolRegistry, ToolSource, DuplicatePolicy, ToolRegistration
└── tools/
    ├── __init__.py      → Init del subpaquete
    ├── file_tool.py     → FileTool (read_file, write_file, list_directory)
    ├── system_tool.py   → SystemTool (info, status)
    └── terminal_tool.py → TerminalTool (run)
```

### 8.2 Ciclo de Vida de una Herramienta

```
nueva BaseTool()
    │
    ▼  state = AVAILABLE
herramienta lista para ejecutar
    │
    ▼  tool.execute(params, context={...})
    │   ├─ validate_input(params)  → ToolError si falla → state = ERROR
    │   ├─ state = EXECUTING
    │   ├─ _execute(params, context) → resultado
    │   ├─ validate_output(result) → ToolError si falla → state = ERROR
    │   └─ state = COMPLETED (o ERROR si excepción)
    │
    ▼  (puede ejecutarse de nuevo desde AVAILABLE/COMPLETED/ERROR)
    │
    ▼  tool.disable()
state = DISABLED  →  tool.enable()  →  state = AVAILABLE
```

### 8.3 Flujo de Ejecución Segura

```
Agente
  │
  ▼  solicita herramienta al Orchestrator
Orchestrator.execute_tool(tool_name, params, session_id)
  │
  ▼  ToolExecutor.execute(tool, params, session_id, agent_name, timeout)
  │
  ├─ emit: tool.requested
  ├─ PermissionService.evaluate_tool(tool_name, level=..., agent_name=...)
  │
  ├─ decision == "deny" → emit: tool.denied → return ToolExecutionResult
  ├─ decision == "confirm" → return ToolExecutionResult (esperar aprobación)
  ├─ decision == "allow" → asyncio.wait_for(tool.execute(params, context), timeout)
  │       │
  │       ├─ éxito → emit: tool.executed → return ToolExecutionResult
  │       ├─ TimeoutError → cancel task → emit: tool.failed → return
   │       ├─ CancelledError → emit: tool.failed → return
   │       └─ Exception → emit: tool.failed → return
  │
  ▼  ToolExecutionResult → Orchestrator → WebSocket → Desktop
```

### 8.4 Niveles de Permiso y Modos

El `PermissionService` tiene dos modos (Confirmation/Automatic) y tres
niveles de herramienta (LOW/MEDIUM/HIGH).

**Modo Confirmation:** toda acción requiere aprobación del usuario,
independientemente del nivel.

**Modo Automatic:**
- Herramientas con nivel <= `auto_allow_up_to` se ejecutan sin preguntar.
- Herramientas en `allowed_commands` se ejecutan sin preguntar.
- Herramientas en `denied_commands` siempre se bloquean (prioridad máxima).
- Todo lo demás requiere confirmación.

El umbral `auto_allow_up_to` se configura con
`permission_service.set_auto_allow_up_to("medium")`.

**Auditoría:** cada evaluación se registra en `permission_service.audit_log`
con timestamp, herramienta, agente, decisión y razón.

### 8.5 Herramientas Base Disponibles

| Herramienta | Categoría | Nivel | Acciones |
|---|---|---|---|
| `FileTool` | FILE | MEDIUM | `read_file`, `write_file`, `list_directory` |
| `SystemTool` | SYSTEM | LOW | `info`, `status` |
| `TerminalTool` | SHELL | HIGH | `run` |

### 8.6 Cómo Crear una Nueva Herramienta

1. Crear un archivo en `packages/tools/src/tools/`.
2. Heredar de `BaseTool`:

   ```python
   from src.base import BaseTool, PermissionLevel, ToolCategory

   class MiHerramienta(BaseTool):
       name = "mi_herramienta"
       description = "Hace algo útil"
       version = "1.0.0"
       category = ToolCategory.CUSTOM
       permission_level = PermissionLevel.LOW

       parameters_schema = {
           "required": ["dato"],
           "properties": {
               "dato": {"type": "str", "description": "El dato"},
           },
       }

       async def _execute(self, params, context):
           # Acceder a contexto:
           session_id = context.get("session_id", "")
           agent_name = context.get("agent_name", "")
           return {"success": True, "output": f"Procesado: {params['dato']}"}
   ```

3. Registrar en el sistema:

   ```python
   from src import ToolRegistry, ToolSource, MiHerramienta

   registry = ToolRegistry()
   registry.register(MiHerramienta(), source=ToolSource.INTERNAL)
   ```

4. Registrar también en el Orchestrator si se usa la herramienta desde agentes:

   ```python
   tool = MiHerramienta()
   orch.register_tool(tool)
   ```

5. (Opcional) Agregar reglas de permisos en `config/development.json`.

## 9. Orchestrator Avanzado — Detalle Técnico (Sprint 1.7)

Sprint 1.7 convierte al Orchestrator en el centro inteligente del
sistema, capaz de planificar, coordinar y supervisar la ejecución
de múltiples agentes, herramientas y tareas de forma segura,
escalable y observable.

### 9.1 Visión General

```
Usuario
  │
  ▼
Orchestrator.handle_message()
  │
  ▼
ExecutionPipeline (único punto de entrada)
  │
  ├─ 1. ValidateRequest       → valida mensaje y sesión
  ├─ 2. PublishUserMessage     → emite user.message.received
  ├─ 3. PersistUserTurn        → SessionManager.append_turn(role="user")
  ├─ 4. BuildContext           → contexto desde sesión
  ├─ 5. RetrieveMemory         → contexto desde memoria persistente
  ├─ 6. PlanExecution          → Planner (opcional)
  ├─ 7. SelectAgent            → AgentRouter.route()
  ├─ 8. CheckPermissions       → (reservado)
  ├─ 9. ExecuteAgent           → asyncio.wait_for(agent.handle, timeout)
  ├─ 10. PublishAgentResult    → emite agent.completed
  ├─ 11. UpdateMemory          → SessionManager.append_turn(role="assistant")
  │
  ▼  Envelope {type, content, status, agent_name, duration_ms, stage_timings}
WebSocket → Cliente
```

El pipeline es **único** y **obligatorio**: ya no existen rutas
duplicadas para procesar un mensaje. Toda la lógica está en
`packages/core/src/pipeline.py` y se invoca desde
`Orchestrator.handle_message()`.

### 9.2 Componentes Nuevos

| Módulo | Archivo | Responsabilidad |
|---|---|---|
| `ExecutionPipeline` | `pipeline.py` | Orquesta las etapas del flujo |
| `Task` + `TaskManager` | `task.py` | Unidad de trabajo direccionable |
| `Workflow` + `WorkflowManager` | `workflow.py` | Motor de DAGs |
| `Planner` + `Plan` + `SubTask` | `planner.py` | Planificación heurística |
| `MetricsCollector` | `observability.py` | Métricas y auditoría |
| `StructuredLogger` | `observability.py` | Logs JSON con correlation_id |

### 9.3 Execution Pipeline

#### Etapas

```python
DEFAULT_STAGES = [
    stage_validate_request,        # valida entrada
    stage_publish_user_message,    # emite user.message.received
    stage_persist_user_turn,       # persiste turno usuario
    stage_build_context,           # construye contexto
    stage_retrieve_memory,         # recupera memoria
    stage_plan_execution,          # invoca Planner (opcional)
    stage_select_agent,            # AgentRouter.route()
    stage_check_permissions,       # gate de permisos (reservado)
    stage_execute_agent,           # ejecuta con timeout
    stage_publish_agent_result,    # emite agent.completed
    stage_update_memory,           # persiste turno asistente
]
```

#### Manejo de Errores

Todas las etapas lanzan `PipelineError(stage, error_type, message)`.
El `ExecutionPipeline.run_safe()` captura estos errores, emite
`agent.failed` con el contexto de la etapa, persiste un turno de
sistema y retorna un envelope de error consistente:

```python
{
    "type": "error",
    "content": "Lo siento, ocurrió un error al procesar tu mensaje.",
    "status": "failed",
    "session_id": "s1",
    "error": "agent_timeout",          # error_type o agent_timeout
    "stage": "stage_execute_agent",    # qué etapa falló
    "duration_ms": 0,
}
```

#### Timing por Etapa

El pipeline registra el tiempo de cada etapa en
`result["stage_timings"]`:

```python
{
    "stage_validate_request": 0,
    "stage_publish_user_message": 1,
    "stage_persist_user_turn": 2,
    "stage_build_context": 0,
    "stage_retrieve_memory": 5,
    "stage_plan_execution": 0,
    "stage_select_agent": 1,
    "stage_check_permissions": 0,
    "stage_execute_agent": 42,
    "stage_publish_agent_result": 1,
    "stage_update_memory": 2,
}
```

Esto permite identificar cuellos de botella por etapa.

### 9.4 Task System

#### Modelo de Tarea

```python
@dataclass
class Task:
    id: str               # UUID4
    name: str
    description: str
    priority: TaskPriority  # LOW | NORMAL | HIGH | CRITICAL
    state: TaskState        # PENDING | READY | RUNNING | WAITING | COMPLETED | FAILED | CANCELLED
    progress: int           # 0..100
    agent_name: str
    tools_used: list[str]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result: Any
    errors: list[str]
    metadata: dict
    parent_id: str | None   # para subtareas
    workflow_id: str | None # para workflows
```

#### Máquina de Estados

```
PENDING → READY → RUNNING ─┬─→ COMPLETED
              ↑             ├─→ FAILED
              │             ├─→ CANCELLED
              └─ WAITING ───┘
                          (paused / blocked)

FAILED → READY (retry)
COMPLETED, CANCELLED: terminal
```

Las transiciones son validadas: no se puede pausar una tarea
`COMPLETED`, ni reanudar una que no esté en `WAITING`.

#### TaskManager

| Método | Descripción |
|---|---|
| `create(name, ...)` | Crea una tarea en `PENDING` |
| `start(task_id)` | Transiciona a `RUNNING` |
| `complete(task_id, result)` | Marca `COMPLETED` |
| `fail(task_id, error)` | Marca `FAILED` |
| `cancel(task_id, reason)` | Marca `CANCELLED` |
| `pause(task_id)` | Pasa a `WAITING` |
| `resume(task_id)` | Pasa de `WAITING` a `READY` |
| `retry(task_id)` | Pasa de `FAILED` a `READY` |
| `update_progress(task_id, pct)` | Actualiza progreso (0..100) |
| `next_ready()` | Devuelve la tarea `READY` de mayor prioridad |
| `summary()` | Stats agregadas para observabilidad |

Cada método emite su evento correspondiente (`task.created`,
`task.started`, etc.).

### 9.5 Workflow Engine

#### DAG (Grafo Dirigido Acíclico)

Un `Workflow` es un conjunto de nodos (`WorkflowNode`) donde cada
nodo referencia una `Task` y declara sus dependencias. El motor
valida el DAG al construirlo (Kahn's algorithm para detectar ciclos).

```
Sequential:           Parallel / Fan-out:
  A → B → C             A
                          ↘
                           B   →  D
                          ↗
                          C
```

#### WorkflowBuilder

API fluida para construir workflows:

```python
wf = await (
    WorkflowBuilder("ingest")
    .with_task_manager(tm, workflow_id="wf-1")
    .add_task("fetch", agent_name="fetcher")
    .add_task("parse", depends_on=["fetch"], agent_name="parser")
    .add_task("store", depends_on=["parse"], agent_name="storer")
    .build_async()
)
```

#### WorkflowManager

| Método | Descripción |
|---|---|
| `register(wf)` | Registra un workflow (emite `workflow.created`) |
| `run(wf, executor)` | Ejecuta el DAG hasta terminar |
| `cancel(name, reason)` | Solicita cancelación (no bloqueante) |

El `executor` es una corrutina `(task) -> result` provista por el
caller. Esto mantiene el motor agnóstico al backend de ejecución.

#### Reintentos

Cada `WorkflowNode` declara `retries=N`. Si la tarea falla, se
reintenta hasta N veces antes de declarar el nodo como `FAILED`.

#### Criticidad

Un nodo `critical=True` (default) aborta el workflow si falla. Un
nodo `critical=False` se marca como `CANCELLED` pero el workflow
continúa con los nodos que no dependan de él.

### 9.6 Planner

#### Estrategias Heurísticas

| Estrategia | Trigger | Subtareas |
|---|---|---|
| `single` | (default) | 1: `handle` |
| `file_operation` | "lee/escribe/lista archivo" | 3: `read → process → respond` |
| `terminal_command` | "ejecuta/corre comando", "terminal/bash" | 3: `validate → execute → respond` |
| `research_then_summarize` | "investiga ... y resume" | 3: `gather → analyze → summarize` |
| `system_info` | "información del sistema" | 2: `query → respond` |

#### Plan

```python
@dataclass
class Plan:
    strategy: str           # etiqueta
    rationale: str          # explicación
    subtasks: list[SubTask] # nodos del plan
```

#### SubTask

```python
@dataclass
class SubTask:
    name: str
    description: str
    suggested_agent: str
    suggested_tools: list[str]
    depends_on: list[str]   # nombres de subtareas hermanas
```

#### Integración con Memoria, AgentRegistry y ToolRegistry

El Planner acepta opcionalmente:
- `memory`: MemoryManager para recuperar contexto histórico.
- `agent_registry`: para conocer capacidades de agentes.
- `tool_registry`: para filtrar herramientas sugeridas.

#### Planificación Basada en LLM (Futuro)

La interfaz `Planner.plan(message, context) -> list[dict]` está
diseñada para que un planner basado en LLM (Sprint 2+) pueda
reemplazar el heurístico sin cambiar callers.

### 9.7 Observabilidad

#### MetricsCollector

Subscrito al EventBus, mantiene:

**Contadores:**
- `tasks_created`, `tasks_started`, `tasks_completed`, `tasks_failed`, `tasks_cancelled`, `tasks_paused`, `tasks_resumed`
- `task_retries`, `task_errors_by_type.{type}`
- `workflows_created`, `workflows_started`, `workflows_completed`, `workflows_failed`, `workflows_cancelled`
- `agent_invocations`, `agent_completions`, `agent_failures`
- `agent_invocations.{name}`, `agent_errors_by_type.{type}`
- `tool_requests`, `tool_executions`, `tool_failures`, `tool_denials`
- `tool_requests.{name}`, `tool_executions.{name}`, `tool_failures.{name}`, `tool_denials.{name}`
- `tool_errors_by_type.{type}`

**Histogramas:**
- `agent_duration_ms.total`, `agent_duration_ms.{agent_name}`
- `tool_duration_ms.total`, `tool_duration_ms.{name}`

**Audit Trail:**
- Buffer circular de los últimos 500 eventos con timestamp y payload.

#### Snapshot

```python
metrics.snapshot()
# {
#   "counters": {"tasks_created": 5, ...},
#   "histograms": {"agent_duration_ms.echo": {"count": 5, "avg": 12.4, ...}},
#   "audit_count": 23,
#   "audit_last_20": [...],
# }
```

#### StructuredLogger

Logger que emite una línea JSON por registro:

```json
{"ts": 1720900000.123, "level": "INFO", "logger": "orchestrator",
 "msg": "message received", "task_id": "t1", "session_id": "s1",
 "correlation_id": "cid-1"}
```

Soporta `correlation_id` para trazabilidad distribuida y métodos
`with_correlation_id(cid)` para crear loggers derivados.

#### AuditRecorder

Buffer en memoria para tests y debugging. Expone `by_type(event_type)`
para filtrar entradas por tipo de evento.

### 9.8 Catálogo de Eventos Extendido

Sprint 1.7 añade los siguientes eventos al catálogo:

| Evento | Emitido por | Cuándo |
|---|---|---|
| `task.created` | TaskManager | Nueva tarea |
| `task.started` | TaskManager | Tarea pasa a RUNNING |
| `task.completed` | TaskManager | Tarea COMPLETED |
| `task.failed` | TaskManager / Pipeline | Tarea FAILED |
| `task.cancelled` | TaskManager | Tarea CANCELLED |
| `task.paused` | TaskManager | Tarea pasa a WAITING |
| `task.resumed` | TaskManager | Tarea vuelve a READY |
| `workflow.created` | WorkflowManager | Workflow registrado |
| `workflow.started` | WorkflowManager | Workflow comienza ejecución |
| `workflow.completed` | WorkflowManager | Workflow terminó OK |
| `workflow.failed` | WorkflowManager | Workflow falló |
| `workflow.cancelled` | WorkflowManager | Workflow cancelado |
| `planner.plan.created` | Planner | Plan generado |
| `planner.decision` | Planner | Una decisión por subtarea |
| `metric.recorded` | MetricsCollector | Métrica registrada (futuro) |

### 9.9 Ejemplo Completo: Workflow con Planner

```python
import asyncio
from src import (
    EventBus, Orchestrator, TaskManager, WorkflowBuilder,
    WorkflowManager, Planner, MetricsCollector,
)

async def main():
    bus = EventBus()
    metrics = MetricsCollector(bus)
    await metrics.start()

    orch = Orchestrator(event_bus=bus)
    orch.attach_planner(Planner(event_bus=bus))
    await orch.initialize()

    # El usuario pide algo que dispara el Planner.
    plan = await orch.planner.plan_detailed(
        "Lee el archivo /etc/hosts",
        context={"session_id": "s1"},
    )

    # Construir un workflow desde el plan.
    tm = TaskManager(event_bus=bus)
    builder = WorkflowBuilder("file_read").with_task_manager(tm)
    for st in plan.subtasks:
        builder.add_task(
            st.name,
            agent_name=st.suggested_agent or "echo",
            depends_on=st.depends_on,
        )
    wf = await builder.build_async()

    # Ejecutar el workflow.
    wfm = WorkflowManager(tm, event_bus=bus)

    async def executor(task):
        # Aquí se invocaría al Orchestrator para cada subtarea.
        return f"result-{task.name}"

    final = await wfm.run(wf, executor)
    print(f"Workflow final state: {final.state.value}")

    # Métricas.
    snap = metrics.snapshot()
    print(f"Total tasks created: {snap['counters']['tasks_created']}")

asyncio.run(main())
```

### 9.10 Decisiones de Diseño (Sprint 1.7)

| Decisión | Justificación |
|---|---|
| Pipeline único centralizado | Elimina rutas duplicadas, centraliza errores |
| Etapas como corrutinas separadas | Facilita tests, reutilización y reemplazo |
| `PipelineError` con `stage` y `error_type` | Trazabilidad granular de fallos |
| Task como unidad direccionable | Permite pausa/reanudación/cancelación |
| TaskManager storage-only | Separación clara entre estado y ejecución |
| Workflow como DAG validado | Previene ciclos, permite paralelismo |
| `executor` inyectado en WorkflowManager.run | Motor agnóstico al backend |
| Planner heurístico primero | Funciona sin LLM; interfaz LLM-ready |
| Métricas en proceso (no Prometheus) | Sin dependencias externas; exporter futuro |
| Audit buffer circular | Buffer en memoria acotado (500 entradas) |
| StructuredLogger con correlation_id | Trazabilidad distribuida lista para usar |

### 9.11 Riesgos y Limitaciones Actuales

| Riesgo | Mitigación |
|---|---|
| TaskManager crece sin límite | LRU en `_history` (MAX_HISTORY=200) |
| WorkflowManager no persiste DAGs | Pendiente: persistencia en Sprint 1.8+ |
| Planner heurístico es limitado | LLM planner en Sprint 2 |
| Métricas en RAM se pierden al reiniciar | Pendiente: exporter Prometheus en futuro |
| `_active_tasks` por nombre en ToolExecutor | Pendiente: keyed por execution_id |

### 9.12 Próximos Pasos (Sprint 1.8)

- Integrar el Planner en el pipeline por defecto (cuando un LLM
  esté disponible).
- Persistir workflows en SQLite para recuperación tras restart.
- Exportador Prometheus para métricas.
- Scheduler basado en prioridades para TaskManager.
- Integración completa del sistema de permisos en el pipeline.

## 10. Sprint 1.8 — Persistencia, Scheduler y Resiliencia

Sprint 1.8 completa la integración de todos los subsistemas construidos
en sprints anteriores, añadiendo persistencia durable, planificación
temporal, recuperación ante fallos y observabilidad avanzada. El
resultado es un sistema resiliente que sobrevive reinicios, programa
tareas futuras y ofrece visibilidad completa de su estado.

### 10.1 Repositorios de Persistencia

Los repositorios permiten almacenar y recuperar Tasks, Workflows y
Execution History en SQLite, eliminando la pérdida de estado ante
reinicios del proceso.

```
packages/memory/src/memory/repositories/
├── __init__.py                  → API pública de repositorios
├── task_repository.py           → TaskRepository
├── workflow_repository.py       → WorkflowRepository
└── execution_repository.py      → ExecutionRepository
```

#### TaskRepository

| Método | Descripción |
|---|---|
| `create(task)` | Inserta una tarea nueva en SQLite |
| `get(task_id)` | Recupera una tarea por ID |
| `update(task)` | Actualiza estado, progreso, resultado |
| `list_by_workflow(workflow_id)` | Tareas pertenecientes a un workflow |
| `list_by_state(state)` | Tareas filtradas por estado |
| `delete(task_id)` | Elimina una tarea |
| `cleanup_old(days)` | Elimina tareas completadas antiguas |

#### WorkflowRepository

| Método | Descripción |
|---|---|
| `create(workflow)` | Inserta un workflow con su definición DAG |
| `get(workflow_id)` | Recupera un workflow por ID |
| `update_state(workflow_id, state)` | Actualiza estado del workflow |
| `list_all()` | Lista todos los workflows |
| `delete(workflow_id)` | Elimina un workflow |

#### ExecutionRepository

| Método | Descripción |
|---|---|
| `record(execution)` | Registra una ejecución completada |
| `get_by_task(task_id)` | Historial de ejecuciones de una tarea |
| `get_by_workflow(workflow_id)` | Historial de ejecuciones de un workflow |
| `get_recent(limit)` | Últimas N ejecuciones |
| `summary()` | Estadísticas agregadas |

#### Migración SQL

La migración `sprint_1_8_tasks_workflows.sql` crea las tablas
`tasks`, `workflows`, `workflow_nodes`, `task_executions` y
`execution_history` con índices apropiados.

### 10.2 Motor de Scheduler

El Scheduler permite programar tareas para ejecución futura,
periódica o con reintentos automáticos.

```
packages/core/src/scheduler.py
```

#### SchedulerJob

```python
@dataclass
class SchedulerJob:
    id: str                       # UUID4
    name: str                     # Nombre descriptivo
    task_id: str | None           # Tarea asociada (opcional)
    action: Callable              # Corrutina a ejecutar
    schedule_type: ScheduleType   # ONCE | PERIODIC | RETRY
    interval_seconds: float       # Intervalo para PERIODIC
    max_retries: int              # Reintentos para RETRY
    retry_delay: float            # Demora entre reintentos
    scheduled_at: datetime        # Momento de ejecución (ONCE)
    created_at: datetime
    state: SchedulerJobState      # PENDING | SCHEDULED | RUNNING | COMPLETED | FAILED | CANCELLED
```

#### Scheduler

| Método | Descripción |
|---|---|
| `schedule(job)` | Programa una nueva tarea |
| `schedule_once(name, action, at)` | Ejecución única en momento futuro |
| `schedule_periodic(name, action, interval)` | Ejecución periódica |
| `schedule_retry(name, action, max_retries, delay)` | Reintentos con demora |
| `cancel(job_id)` | Cancela un job programado |
| `get_jobs()` | Lista todos los jobs |
| `start()` | Inicia el loop del scheduler |
| `stop()` | Detiene el scheduler |

#### SchedulerRepository

Persiste los jobs del scheduler en SQLite para sobrevivir reinicios.
Al arrancar, el Scheduler restaura jobs `PENDING` y `SCHEDULED`
desde el repositorio.

```
Scheduler.schedule()
  │
  ├─ SchedulerRepository.persist(job)
  ├─ asyncio.create_task(_run_job(job))
  │     │
  │     ├─ job.state = RUNNING
  │     ├─ await job.action()
  │     ├─ job.state = COMPLETED
  │     └─ (o) job.state = FAILED → retry logic
  │
  └─ emit: scheduler.job.{scheduled,completed,failed}
```

### 10.3 Sistema de Recuperación

El sistema de recuperación permite al Orchestrator y sus componentes
sobrevivir a reinicios, reanudando tareas y workflows desde su último
checkpoint.

```
packages/core/src/recovery.py
```

#### RecoveryManager

| Método | Descripción |
|---|---|
| `create_checkpoint(entity_type, entity_id, data)` | Guarda un checkpoint |
| `get_latest(entity_type, entity_id)` | Recupera el último checkpoint |
| `list_checkpoints(entity_type, entity_id)` | Historial de checkpoints |
| `delete_checkpoints(entity_type, entity_id)` | Limpia checkpoints |
| `recover_all()` | Reanuda todas las entidades con checkpoints activos |
| `recover_task(task_id)` | Reanuda una tarea específica |
| `recover_workflow(workflow_id)` | Reanuda un workflow específico |

#### CheckpointManager

Gestiona el ciclo de vida de los checkpoints:

```python
@dataclass
class Checkpoint:
    id: str
    entity_type: str          # "task" | "workflow"
    entity_id: str
    state: dict               # Estado serializable
    created_at: datetime
    metadata: dict
```

- Los checkpoints se almacenan en SQLite vía `ExecutionRepository`.
- Se crea un checkpoint automático antes de cada transición de estado
  crítica (`RUNNING → WAITING`, `COMPLETED`).
- Al recuperar, se restaura el estado y se reanuda la ejecución
  desde el punto guardado.

#### ResilienceHelper

Utilidades transversales para resiliencia:

| Función | Descripción |
|---|---|
| `retry_with_backoff(coro, max_retries, base_delay)` | Reintento exponencial |
| `circuit_breaker(fail_threshold, reset_timeout)` | Circuit breaker pattern |
| `timeout_wrapper(coro, timeout)` | Timeout con cancelación limpia |

#### Flujo de Recuperación

```
Reinicio del proceso
  │
  ▼
RecoveryManager.recover_all()
  │
  ├─ Lista checkpoints en DB
  ├─ Para cada checkpoint activo:
  │     ├─ Restaurar estado
  │     ├─ Reanudar tareas RUNNING → READY
  │     ├─ Reanudar workflows en progreso
  │     └─ Restaurar Scheduler jobs
  │
  ▼
Sistema operativo con estado previo al reinicio
```

### 10.4 Observabilidad Avanzada

Se extiende la observabilidad de Sprint 1.7 con dashboard interno,
exportadores y tracking por entidad.

#### Dashboard Snapshot

```python
metrics.dashboard_snapshot()
# {
#   "system": {
#     "uptime_seconds": 3600,
#     "total_tasks": 42,
#     "active_tasks": 3,
#     "total_workflows": 8,
#     "scheduler_jobs_pending": 5,
#   },
#   "counters": { ... },
#   "histograms": { ... },
#   "recent_errors": [ ... ],
#   "scheduler_status": { "running": true, "jobs": [...] },
#   "recovery_status": { "checkpoints": 12, "last_recovery": "..." },
# }
```

#### Exportadores

| Exportador | Formato | Destino |
|---|---|---|
| `ConsoleExporter` | Texto tabulado | stdout (desarrollo) |
| `JSONExporter` | JSON | Archivo o stdout |
| `PrometheusExporter` | Prometheus text format | HTTP endpoint `/metrics` |

Todos los exportadores implementan la interfaz `MetricsExporter` con
el método `export(snapshot)`.

#### Tracking por Entidad

Las métricas ahora se pueden filtrar por entidad:

```python
metrics.get_entity_metrics("task", "t-123")
# → Contadores, histogramas y auditoría específicos de esa tarea

metrics.get_entity_metrics("workflow", "wf-456")
# → Estado del workflow, duración, tareas hijas
```

### 10.5 Diagrama de Integración Actualizado

```
┌─────────────────────────────────────────────────────────────────────┐
│                          DESKTOP APP (WinUI 3)                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ WebSocket + REST
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         BACKEND (FastAPI)                            │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     ORCHESTRATOR (Core)                       │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌─────────────┐  ┌──────────────────────┐  │   │
│  │  │  Pipeline   │  │  Planner    │  │   ToolExecutor       │  │   │
│  │  │  (11 stages)│  │  (heuristic)│  │  (keyed by exec_id)  │  │   │
│  │  └──────┬─────┘  └──────┬──────┘  └──────────┬───────────┘  │   │
│  │         │               │                     │              │   │
│  │  ┌──────┴───────────────┴─────────────────────┴──────────┐  │   │
│  │  │                  TASK MANAGER                          │  │   │
│  │  │  ┌──────────┐  ┌───────────┐  ┌───────────────────┐  │  │   │
│  │  │  │  Tasks    │  │ Workflows │  │  SCHEDULER        │  │  │   │
│  │  │  │  (7 st.)  │  │  (DAGs)   │  │  (once/periodic/  │  │  │   │
│  │  │  └────┬─────┘  └─────┬─────┘  │   retry)          │  │  │   │
│  │  │       │              │        └────────┬──────────┘  │  │   │
│  │  └───────┼──────────────┼─────────────────┼─────────────┘  │   │
│  └──────────┼──────────────┼─────────────────┼────────────────┘   │
│             │              │                 │                      │
│  ┌──────────┼──────────────┼─────────────────┼────────────────┐   │
│  │          ▼              ▼                 ▼                │   │
│  │              PERSISTENCIA (SQLite)                          │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌───────────────────┐  │   │
│  │  │ TaskRepo     │ │ WorkflowRepo │ │ ExecutionRepo     │  │   │
│  │  │              │ │              │ │ + Checkpoints     │  │   │
│  │  └──────────────┘ └──────────────┘ └───────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  OBSERVABILIDAD                            │   │
│  │  ┌──────────────┐ ┌─────────────┐ ┌───────────────────┐  │   │
│  │  │MetricsCollect│ │ Structured  │ │ RecoveryManager    │  │   │
│  │  │+ Dashboard   │ │ Logger      │ │ + CheckpointMgr    │  │   │
│  │  │+ Exporters   │ │             │ │ + ResilienceHelper │  │   │
│  │  └──────────────┘ └─────────────┘ └───────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │  Agents    │  │   Memory     │  │   Tools      │             │
│  │  Registry  │  │  Manager     │  │  Registry    │             │
│  └────────────┘  └──────────────┘  └──────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

### 10.6 Deuda Técnica

La deuda técnica identificada y resuelta en este sprint está
documentada en detalle en `docs/technical-debt.md`. Los ítems
principales resueltos son:

| Ítem | Estado | Detalle |
|---|---|---|
| `ToolExecutor._active_tasks` keyed por nombre | ✅ Resuelto | Ahora keyed por `execution_id` |
| `PermissionMode` inconsistente entre enums y config | ✅ Resuelto | Unificado en `packages/shared/src/enums.py` |
| Métricas se pierden al reiniciar | ⚠️ Mitigado | Exportadores disponibles; persistencia parcial |
| Tests de aislamiento | ⚠️ Mitigado | 70 nuevos tests de integración |

## 11. Motor LLM — Detalle Técnico (Sprint 1.9)

### 11.1 Visión General

El Sprint 1.9 introduce el paquete `packages/llm/`, el motor de
interacción con modelos de lenguaje. Antes de este sprint el sistema
no tenía conexión real con ningún LLM; los agentes operaban de forma
simulada. A partir de ahora, el Orchestrator puede construir prompts,
gestionar la ventana de contexto, seleccionar proveedores y recibir
respuestas en streaming.

Estructura del paquete:

```
packages/llm/
├── src/llm/
│   ├── __init__.py
│   ├── manager.py          → ModelManager (registro, activación, caché, warmup, health)
│   ├── protocol.py         → LLMProvider (Protocol unificado)
│   ├── context/
│   │   ├── __init__.py
│   │   └── manager.py      → ContextManager (ventana de tokens, trimming, auto-summarization)
│   ├── prompt/
│   │   ├── __init__.py
│   │   └── pipeline.py     → PromptPipeline (construcción unificada de prompts)
│   ├── streaming/
│   │   ├── __init__.py
│   │   └── manager.py      → StreamingManager (streaming con cancelación, timeout, retry)
│   └── providers/
│       ├── __init__.py
│       ├── ollama.py       → OllamaProvider
│       ├── openai.py       → OpenAIProvider
│       ├── anthropic.py    → AnthropicProvider
│       ├── google.py       → GoogleProvider
│       └── openrouter.py   → OpenRouterProvider
```

### 11.2 ModelManager

El `ModelManager` es el punto de entrada central del paquete LLM.
Responsabilidades principales:

| Responsabilidad | Detalle |
|---|---|
| **Registro** | `register_provider(name, provider_cls, config)` — registra un proveedor disponible. |
| **Activación** | `activate(name)` — selecciona el proveedor activo para la sesión. |
| **Selección** | `get_active_provider() -> LLMProvider` — retorna el proveedor actual. |
| **Caché** | Respuestas cacheadas por hash del prompt para evitar llamadas redundantes. |
| **Warmup** | `warmup()` — pre-carga modelos y verifica conectividad al arrancar. |
| **Health checking** | `health_check(name?) -> dict` — diagnostica estado de uno o todos los proveedores. |

```python
manager = ModelManager()
manager.register_provider("ollama", OllamaProvider, {"base_url": "http://localhost:11434"})
manager.register_provider("openai", OpenAIProvider, {"api_key": "sk-..."})
manager.activate("ollama")
provider = manager.get_active_provider()
```

### 11.3 LLMProvider — Protocol Unificado

Todos los proveedores implementan el mismo `Protocol`, lo que permite
intercambiarlos sin modificar el Orchestrator:

```python
class LLMProvider(Protocol):
    async def chat(self, messages: list[dict], **kwargs) -> str: ...
    async def stream(self, messages: list[dict], **kwargs) -> AsyncIterator[str]: ...
    async def embeddings(self, text: str) -> list[float]: ...
    async def tokenize(self, text: str) -> int: ...
    async def health(self) -> bool: ...
    async def close(self) -> None: ...
```

| Método | Propósito |
|---|---|
| `chat()` | Generación completa (non-streaming). |
| `stream()` | Generación en tiempo real via async iterator. |
| `embeddings()` | Vectores de embedding para búsqueda semántica. |
| `tokenize()` | Cuenta de tokens del texto (proxy para contexto). |
| `health()` | Verifica que el endpoint responde. |
| `close()` | Limpia recursos (conexiones HTTP, sesiones). |

### 11.4 Proveedores Implementados

| Proveedor | Clase | Endpoint | Notas |
|---|---|---|---|
| Ollama | `OllamaProvider` | `http://localhost:11434` | Modelos locales, sin API key. |
| OpenAI | `OpenAIProvider` | `https://api.openai.com/v1` | GPT-4o, GPT-4o-mini, etc. |
| Anthropic | `AnthropicProvider` | `https://api.anthropic.com` | Claude 3.5 Sonnet, Claude 3 Opus. |
| Google | `GoogleProvider` | `https://generativelanguage.googleapis.com` | Gemini Pro, Gemini Ultra. |
| OpenRouter | `OpenRouterProvider` | `https://openrouter.ai/api/v1` | Agregador multi-modelo con una sola API key. |

Todos los proveedores utilizan `httpx` como cliente HTTP asíncrono,
con soporte para timeouts configurables y reintentos automáticos.

### 11.5 ContextManager — Gestión de Ventana de Contexto

El `ContextManager` gestiona la ventana de tokens disponible para cada
modelo, asegurando que las conversaciones no excedan los límites:

| Capacidad | Detalle |
|---|---|
| **Ventana de tokens** | Límite configurable por modelo (ej: 8192 para Ollama, 128K para GPT-4o). |
| **Estimación heurística** | Cuenta aproximada de tokens = `len(text) / 4` (español). |
| **Smart trimming** | Elimina mensajes más antiguos preservando el system prompt y los más recientes. |
| **Auto-summarization** | Cuando el historial supera el umbral, resume turnos antiguos en un solo mensaje de contexto. |

```python
ctx = ContextManager(max_tokens=8192)
ctx.add_system("Eres Liz, un asistente IA...")
ctx.add_history([{"role": "user", "content": "..."}, ...])
trimmed = ctx.build_messages()  # -> lista recortada que cabe en la ventana
```

### 11.6 PromptPipeline — Construcción Unificada de Prompts

El `PromptPipeline` orquesta la construcción del prompt final,
combinando múltiples fuentes de información en un orden determinista:

```
PromptPipeline.build(system_prompt, memory, history, plan, tasks, tools)
    │
    ├── 1. System Prompt (instrucciones base del agente)
    ├── 2. Memory (contexto relevante recuperado)
    ├── 3. History (historial de conversación, pasado por ContextManager)
    ├── 4. Plan (plan del Planner, si existe)
    ├── 5. Tasks (tareas pendientes del TaskManager)
    └── 6. Tools (descripciones de herramientas disponibles)
        │
        ▼
    list[dict]  →  mensajes listos para enviar al LLMProvider
```

Esta construcción se integra como la etapa `stage_build_prompt`
dentro del pipeline de ejecución del Orchestrator.

### 11.7 StreamingManager — Streaming en Tiempo Real

El `StreamingManager` gestiona la transmisión incremental de
respuestas del LLM hacia los clientes WebSocket:

| Capacidad | Detalle |
|---|---|
| **Cancelación** | El cliente puede cancelar el stream en cualquier momento. |
| **Timeout** | Timeout configurable por proveedor y por solicitud. |
| **Retry** | Reintentos automáticos con backoff exponencial ante errores transitorios. |
| **Backpressure** | Control de flujo para evitar saturar el buffer del cliente. |
| **Multi-cliente** | Múltiples clientes pueden suscribirse al mismo stream. |

```python
stream_mgr = StreamingManager()
async for chunk in stream_mgr.stream_message(provider, messages):
    await websocket.send_text(chunk)  # envío incremental al desktop
```

### 11.8 Puntos de Integración con el Orchestrator

El motor LLM se integra con el sistema existente en tres puntos
clave:

| Punto de integración | Detalle |
|---|---|
| `Orchestrator.attach_llm(manager)` | Inyecta el `ModelManager` en el Orchestrator. |
| `pipeline: stage_build_prompt` | Nueva etapa del pipeline que invoca `PromptPipeline.build()`. |
| `stream_message()` | Método del Orchestrator que delega en `StreamingManager`. |

### 11.9 Diagrama de Flujo LLM

```
┌──────────┐     ┌───────┐     ┌──────────────┐     ┌───────────────┐
│  Usuario  │────►│  API  │────►│ Orchestrator │────►│ PromptPipeline│
└──────────┘     └───────┘     └──────┬───────┘     └───────┬───────┘
                                              │                     │
                                              │    Combina:         │
                                              │    system + memory  │
                                              │    + history + plan │
                                              │    + tasks + tools  │
                                              │                     ▼
                                              │            ┌────────────────┐
                                              │            │ ContextManager │
                                              │            │ (trim + summary)│
                                              │            └───────┬────────┘
                                              │                    │
                                              │                    ▼
                                              │          ┌──────────────────┐
                                              └─────────►│   LLMProvider    │
                                                         │ (Ollama/OpenAI/  │
                                                         │  Anthropic/      │
                                                         │  Google/         │
                                                         │  OpenRouter)     │
                                                         └────────┬─────────┘
                                                                  │
                                                                  ▼
                                                         ┌──────────────────┐
                                                         │ StreamingManager  │
                                                         │ (chunk a chunk)   │
                                                         └────────┬─────────┘
                                                                  │
                                                                  ▼
                                                         ┌──────────────────┐
                                                         │    Respuesta     │
                                                         │  (al usuario)    │
                                                         └──────────────────┘
```

### 11.10 Decisiones de Diseño (Sprint 1.9)

| Decisión | Justificación |
|---|---|
| Protocol en vez de ABC | Permite que cualquier clase cumpla la interfaz sin herencia obligatoria. |
| `httpx` como cliente HTTP | Async nativo, soporte HTTP/2, mejor que `aiohttp` para streaming. |
| Estimación heurística de tokens | Evita dependencia pesada (tiktoken) en el MVP; se mejorará en Sprint 2.0. |
| PromptPipeline separado del ContextManager | Responsabilidad única: uno construye, el otro recorta. |
| Caché en ModelManager | Evita llamadas duplicadas al mismo modelo con el mismo prompt. |

## 12. Sprint 2.1 — Arquitectura Multi-Agent

> Documentación completa: [`docs/sprint-2.1-multi-agent-report.md`](sprint-2.1-multi-agent-report.md)
> y [`docs/sprint-2.1-diagrams.md`](sprint-2.1-diagrams.md).

Sprint 2.1 introduce una **arquitectura profesional basada en agentes**
en `packages/multiagent/`. Es una **capa paralela** al `Orchestrator`
de Sprint 1: no reemplaza código existente, añade una nueva capa
lista para integrarse mediante adaptadores.

### 12.1 Componentes

- **`BaseAgent`** (abstracta) — identidad, permisos, memoria, herramientas,
  objetivos, lifecycle (`start/stop/pause/resume/execute/health/serialize`).
- **`AgentOrchestrator`** — registro, dispatch, broadcast, pause/resume
  global, health check, métricas, heartbeat.
- **`MessageBus`** — pub/sub async entre agentes con broadcast y pending.
- **`PriorityQueue`** — cola de tareas con estados
  queued/running/paused/completed/failed/cancelled y retry.
- **`AgentMemory`** — memoria corta (deque) + larga (LRU) + embeddings
  (cosine similarity, sin dependencias externas).
- **`AgentLogger`** — logs estructurados (start/end/error/tool/metric)
  con duration_ms, memory_kb, tokens, tools_used.
- **`MultiAgentConfig`** — config inmutable (max_agents, timeouts,
  retry, heartbeat, capacidades).

### 12.2 Agentes concretos

| Agente | Responsabilidad |
|---|---|
| `PlannerAgent` | Divide tareas, estima dificultad, prioriza (heurística por patrones). |
| `CoderAgent` | Escribe, refactoriza, corrige y genera tests. |
| `ReviewerAgent` | Detecta malas prácticas, seguridad, performance, calidad. |
| `ResearchAgent` | Investiga, busca APIs, resume (con fetch HTTP opcional). |
| `TerminalAgent` | Ejecuta comandos con allowlist + bloqueo de tokens peligrosos. |
| `GitAgent` | Commits, ramas, merges, push, pull (force requiere UNRESTRICTED). |
| `MemoryAgent` | Memoria corta/larga, embeddings, recuperación contextual. |

### 12.3 Compatibilidad con Sprint 1

| Sprint 1 | Sprint 2.1 | Relación |
|---|---|---|
| `packages/agents/src/base.py::BaseAgent` | `packages/multiagent/src/multiagent/base.py::BaseAgent` | Coexisten (interfaces distintas) |
| `packages/core/src/orchestrator.py::Orchestrator` | `packages/multiagent/src/multiagent/orchestrator.py::AgentOrchestrator` | Coexisten |
| `packages/core/src/task.py::Task/TaskManager` | `packages/multiagent/src/multiagent/queue.py::Task/PriorityQueue` | Coexisten |
| `packages/core/src/event_bus.py::EventBus` | `packages/multiagent/src/multiagent/messages.py::MessageBus` | Coexisten |

Los 43+ tests de Sprint 1 verificados siguen pasando sin cambios.


## 13. Sprint 2.2 — Collaborative Multi-Agent Execution

> Documentación completa: [`docs/sprint-2.2-collaborative-agents-report.md`](sprint-2.2-collaborative-agents-report.md)
> y [`docs/sprint-2.2-diagrams.md`](sprint-2.2-diagrams.md).

Sprint 2.2 transforma los agentes en un **equipo colaborativo**. El
`WorkflowOrchestrator` coordina workflows completos entre varios
agentes, con DAG de dependencias, ejecución paralela, failover
automático, contexto compartido y observabilidad completa.

### 13.1 Componentes nuevos

- **`WorkflowEngine`** — motor que ejecuta workflows respetando el DAG,
  con paralelismo configurable y failover (retry/reassign/skip/cancel).
- **`Workflow` / `Step`** — modelos de datos con todos los campos de
  la especificación (id, name, status, retry, timeout, depends_on, etc.).
- **`DAG`** — grafo acíclico con `validate()` (ciclos, deps rotas,
  huérfanos), `topological_order()`, `ready_steps()`, `ancestors()`,
  `descendants()`, `subgraph()`.
- **`EventBus`** — bus **pub/sub de eventos del sistema**, separado del
  `MessageBus` (punto-a-punto). Historial, métricas, filtros, aislamiento.
- **`ContextManager`** — contexto compartido: objetivos, archivos (con
  SHA-256), snippets de código, variables, mensajes, memoria. Soporta
  `transfer_context` entre agentes.
- **`Scheduler`** — decide qué step, qué agente y cuándo. Respeta
  `max_parallel_steps` y `max_parallel_agents`.
- **`LoadBalancer`** — scoring ponderado (estado, CPU, RAM, cola,
  especialidad, historial) con pesos normalizables.
- **`MetricsCollector`** — suscrito al EventBus, construye histograms
  (p50/p95/p99) de workflow_duration, step_duration, queue_time, y
  contadores de parallelism, agent_usage, retry_count, success_rate.
- **`WorkflowOrchestrator`** — API unificada que combina
  `AgentOrchestrator` (Sprint 2.1) + `WorkflowEngine` (Sprint 2.2).
  Métodos de coordinación: `delegate`, `request_help`,
  `transfer_context`, `wait_for`, `cancel_workflow`, `resume_workflow`.
- **API REST** (FastAPI) — endpoints `/workflows`, `/workflows/{id}`,
  `/steps`, `/events`, `/metrics/workflows`, `/metrics/agents`.

### 13.2 PlannerAgent extendido

Con `payload['op'] = 'plan_workflow'`, el `PlannerAgent` genera un
`Workflow` completo con DAG y estimaciones de tokens, duración y
parallelismo. Patrones: file_op, terminal, research, system, review.

### 13.3 Failover

Orden de acciones cuando un step falla:

1. **RETRY** si `step.retry < max_retries`.
2. **REASSIGN** si `reassign_on_fail` y no se han agotado los reassigns.
3. **SKIP** si `criticality == OPTIONAL` y `skip_optional_on_fail`.
4. **CANCEL** si `criticality == REQUIRED` y `cancel_on_required_fail`.

El SKIP se propaga en cadena a los dependientes.

### 13.4 Compatibilidad

| Sprint 2.1 | Sprint 2.2 | Relación |
|---|---|---|
| `AgentOrchestrator` | `WorkflowOrchestrator` (envuelve al anterior) | Compatibles |
| `MessageBus` | `EventBus` (separado) | Coexisten |
| 7 agentes concretos | `PlannerAgent` extendido | 100% compatible |

Los **130 tests de Sprint 2.1** siguen pasando sin cambios.

## 14. Sprint 2.2/2 — Refinamiento: Agent Registry + Capabilities

> Documentación completa: [`docs/sprint-2.2-2-agent-registry-report.md`](sprint-2.2-2-agent-registry-report.md)
> y [`docs/sprint-2.2-2-diagrams.md`](sprint-2.2-2-diagrams.md).

Sprint 2.2/2 elimina dependencias rígidas entre Scheduler, Workflow
Engine y agentes. Los agentes dejan de identificarse únicamente por
nombre y pasan a ser **descubiertos, registrados y seleccionados
automáticamente** según sus **capabilities**.

### 14.1 Componentes nuevos

- **`AgentRegistry`** — registro único de agentes. Métodos
  `register/unregister/replace/discover/get/get_all/exists/health/metrics`.
  Auto-discovery via hooks. Health con heartbeat, uptime, usage.
- **`AgentRecord`** — wrapper con id/name/version/capabilities/priority/
  status/metadata/created_at/last_seen/last_heartbeat/health/errors/usage.
- **`Capability`** (inmutable, jerárquica con dots, wildcards `*`).
- **`CapabilityRegistry`** — catálogo de 34 capabilities estándar en
  10 categorías (planning, workflow, code.*, git.*, terminal.execute,
  filesystem.*, memory.*, web.search, documentation.lookup, research.*).
- **`CapabilityResolver`** — selección inteligente por scoring ponderado
  (specialty + priority + cost + history + load + latency).
- **`BackwardCompatibilityAdapter`** — traduce nombres legacy
  (PlannerAgent, CoderAgent, etc.) y actions (refactor, review, etc.)
  a capabilities. Ningún workflow existente se rompe.
- **`RegistryAwareScheduler`** — scheduler que usa el resolver en lugar
  del LoadBalancer directo. Hereda de `Scheduler` (Sprint 2.2).

### 14.2 Eventos nuevos

`agent.registered`, `agent.removed`, `agent.health.changed`,
`capability.resolved`, `capability.miss`, `scheduler.selected_agent`.

### 14.3 Verificación: nada depende de clases concretas

El `RegistryAwareScheduler` no recibe agentes por nombre; los obtiene
del `AgentRegistry` vía `CapabilityResolver`. El
`BackwardCompatibilityAdapter` traduce workflows legacy automáticamente.
Ningún componente del workflow engine acopla a una clase concreta de
agente.

### 14.4 Compatibilidad

| Componente anterior | Nuevo componente | Relación |
|---|---|---|
| `AgentOrchestrator` (Sprint 2.1) | `AgentRegistry` | Coexisten; registry es fuente de verdad |
| `Scheduler` (Sprint 2.2) | `RegistryAwareScheduler` | Hereda; se puede usar cualquiera |
| `LoadBalancer` (Sprint 2.2) | `CapabilityResolver` | Coexisten; resolver usa scoring más rico |
| Workflows con `step.agent="CoderAgent"` | `BackwardCompatibilityAdapter` | Traduce automáticamente |

Los **248 tests de Sprint 2.1+2.2** siguen pasando sin cambios.
