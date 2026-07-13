# Arquitectura — Liz Coder Plus

> Visión general de la arquitectura del proyecto.
> Versión: `v0.1.2` — Sprint 1.5 (Agent Stabilization).

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

- **BaseTool:** clase abstracta con metadata.
  - **Categorías:** `SHELL`, `FILE`, `WEB`, `APP`, `SYSTEM`, `CUSTOM`
    (enum `ToolCategory`).
  - **Metadata:** `name`, `description`, `version`, `category`.
- **ToolRegistry:** registro con validación del Protocol `Tool`.
- **Seguridad:** toda ejecución pasa por `ToolExecutor` que consulta
  `PermissionService` antes de ejecutar. Decisiones: `allow`,
  `confirm`, `deny`.

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

## 5. Evolución prevista

- **Sprint 2:** primer agente conversacional + herramientas operativas.
- **Sprint 3:** herramientas operativas + permisos en producción.
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
| `packages/tools/` | Paquete completo, sin uso | Sprint 3 |
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
    ▼  ToolExecutor.execute(tool, params)
    │
    ├─→ emit: tool.requested
    ├─→ PermissionService.evaluate(tool_name)
    │
    ├─ decision == "deny" → emit: tool.denied → return
    ├─ decision == "confirm" → return (cliente debe aprobar)
    ├─ decision == "allow" → tool.execute(params)
    │       ├─ éxito → emit: tool.executed
    │       └─ error → emit: tool.failed
    │
    ▼  ToolExecutionResult {tool_name, decision, success, output, error, duration_ms}
```

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
