# Arquitectura — Liz Coder Plus

> Visión general de la arquitectura del proyecto.
> Versión: `v0.1.1` — Sprint 1 - Prompt 1 (Foundation).

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
  agente, coordina memoria y herramientas, y emite eventos.
- **WebSocketManager:** registro de conexiones activas. Soporta
  broadcast global, broadcast por sesión y envío dirigido. Expone
  callbacks `on_connect` / `on_disconnect` para auditoría.
- **SessionManager:** almacena en memoria el historial temporal de
  cada conversación (hasta 200 turnos), el idioma preferido del usuario
  y el último modo de permiso solicitado.
- **AgentRouter:** decide qué agente atiende cada mensaje. Sprint 1
  incluye un `EchoAgent` por defecto que devuelve un saludo en español.
- **EventBus:** bus pub/sub interno para desacoplar módulos.
- **Eventos:** el catálogo está en `packages/core/src/events.py`.

El paquete `core` se expone al backend a través de un shim
`packages/core/liz_core.py` que evita colisiones de namespace `src`
entre el backend y el core.

### 2.4 Memory (`packages/memory`)

- **Backend inicial:** SQLite (vía SQLAlchemy 2.0 + aiosqlite).
- **Tipos:**
  - Corto plazo: contexto de la conversación actual.
  - Largo plazo: hechos, preferencias y resúmenes.
- **Contrato:** cualquier backend debe implementar el Protocol
  `MemoryBackend` de `packages/memory/src/base.py`.

### 2.5 Agents (`packages/agents`)

- **Agentes especializados:** conversacional, código, sistema, etc.
- **Contrato:** heredan de `BaseAgent` (`packages/agents/src/base.py`).
- **Registro:** el `AgentRegistry` mantiene el catálogo disponible.

### 2.6 Tools (`packages/tools`)

- **Herramientas externas:** shell, archivos, web, automatización.
- **Contrato:** heredan de `BaseTool` (`packages/tools/src/base.py`).
- **Seguridad:** toda ejecución pasa antes por el `PermissionService`.

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
   - Persiste el turno del usuario en el `SessionManager`.
   - Consulta el `AgentRouter` para seleccionar un agente.
   - Invoca al agente, que produce una respuesta de texto.
   - Persiste el turno del asistente.
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
| EventBus interno                | Desacopla módulos y habilita auditoría.                    |
| Permisos en backend             | El desktop nunca ejecuta acciones peligrosas.              |
| Configuración por entorno       | Diferencia desarrollo / producción.                        |

## 5. Evolución prevista

- **Sprint 2:** memoria SQLite funcional + primer agente.
- **Sprint 3:** herramientas operativas + permisos en producción.
- **Sprint 4:** UI desktop con chat.
- **Sprint 5:** integración real con modelos IA.
- **Sprint 6+:** voz, imágenes, control avanzado del PC.
