# Liz Coder Plus - Backend

> Servidor Python construido con **FastAPI** y **WebSocket**.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto del proyecto.

## Arquitectura

```
src/
├── api/             → Endpoints REST y WebSocket
├── core/            → Orquestador, eventos, configuración
├── events/          → Sistema de eventos interno
├── permissions/     → Sistema de permisos
└── main.py          → Punto de entrada de la aplicación
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
pytest -v --cov=src
```

## Próximos pasos (Sprint 1 - Prompt 2)

- Implementar endpoints de salud (`/health`, `/status`).
- Stub de WebSocket para comunicación con la app desktop.
- Cargar configuración desde `config/`.
- Conectar al orquestador `core`.
