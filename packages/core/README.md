# Core Package

> Orquestador principal del sistema Liz Coder Plus.

## Responsabilidades

- Recibir entradas del usuario desde la API.
- Decidir qué agente(s) invocar (AgentRouter).
- Coordinar el acceso a memoria (SessionManager).
- Gestionar conexiones WebSocket (WebSocketManager).
- Despachar herramientas externas (Sprint 3).
- Emitir eventos a través del bus interno (EventBus).

## Estado

**Sprint 1 - Prompt 2 (WebSocket layer)** — WebSocketManager, SessionManager,
AgentRouter y Orchestrator operativos. EchoAgent por defecto.

## Estructura

```
src/
├── __init__.py        → Re-exports públicos
├── orchestrator.py    → Orchestrator (WS + Session + Router + streaming)
├── protocols.py       → Protocol Agent / Memory / Tool
├── agent_router.py    → AgentRouter + EchoAgent por defecto
├── session_manager.py → SessionManager (historial + estado de usuario)
├── ws_connection.py   → Protocol WebSocketConnection
├── ws_manager.py      → WebSocketManager (conexiones, broadcast, eventos)
└── events.py          → Catálogo de eventos del sistema
```

## Shim `liz_core.py`

El archivo `packages/core/liz_core.py` expone el API público del paquete
sin requerir `pip install -e`. El backend lo usa así:

```python
import sys
sys.path.insert(0, "packages/core")
from liz_core import Orchestrator, WebSocketManager
```

## Tests

Los tests unitarios viven en `tests/unit/` y cubren:

- `test_ws_manager.py` — registro, broadcast, broadcast por sesión,
  unregister, callbacks, manejo de fallos.
- `test_session_manager.py` — creación, historial con límite,
  persistencia de modo/idioma, snapshot, drop.
- `test_agent_router.py` — routing por reglas, fallback a EchoAgent,
  orden de evaluación, excepciones en predicados.
- `test_orchestrator.py` — handle_message, streaming, persistencia
  de modo, manejo de errores de agente.
