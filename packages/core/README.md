# Core Package

> Orquestador principal del sistema Liz Coder Plus.

## Responsabilidades

- Recibir entradas del usuario desde la API.
- Decidir qué agente(s) invocar.
- Coordinar el acceso a memoria.
- Despachar herramientas externas.
- Emitir eventos a través del bus interno.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto. Implementación real en Sprint 2.

## Estructura

```
src/
├── __init__.py
├── orchestrator.py    → Clase Orchestrator (stub)
└── events.py          → Definición de eventos del sistema
```
