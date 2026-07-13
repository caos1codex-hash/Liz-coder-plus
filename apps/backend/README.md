# Liz Coder Plus - Backend

> Servidor Python construido con **FastAPI** y **WebSocket**.

## Estado

**Sprint 1 - Prompt 2 (WebSocket layer)** — Backend con WebSocket funcional.

## Arquitectura

```
src/
├── api/             → Endpoints REST y WebSocket
│   ├── main.py      → App FastAPI + lifespan + /health + /status
│   ├── ws_adapter.py → Adaptador FastAPI ↔ WebSocketConnection
│   └── ws_routes.py → Endpoint /ws/chat + WebSocketManager global
├── core/            → Orquestador, eventos, configuración
├── events/          → Sistema de eventos interno
├── permissions/     → Sistema de permisos
└── __init__.py      → Versión del paquete
```

## Requisitos

- Python 3.11+
- pip o cualquier gestor de paquetes Python

## Instalación

```bash
cd apps/backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Documentación interactiva:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Pruebas

```bash
# Desde la raíz del repositorio:
python -m pytest tests/ -v
```

Los tests cubren:
- Endpoints REST `/health` y `/status`.
- WebSocket `/ws/chat`: conexión, envío, recepción streaming,
  desconexión limpia, validación de errores.
- WebSocketManager, SessionManager, AgentRouter, Orchestrator
  (tests unitarios sin red).

## Próximos pasos (Sprint 2)

- Memoria SQLite persistente para el SessionManager.
- Primer agente conversacional real (más allá de EchoAgent).
- Integración con modelos IA.
