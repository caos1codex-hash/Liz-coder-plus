# Memory Package

> Sistema de memoria persistente para Liz Coder Plus.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto. Implementación real en Sprint 2.

## Diseño

- **Backend inicial:** SQLite (vía SQLAlchemy + aiosqlite).
- **Tipos de memoria:**
  - Corto plazo: contexto de la conversación actual.
  - Largo plazo: hechos, preferencias y resúmenes entre sesiones.
- **Contrato:** cualquier backend debe implementar el Protocol `Memory`
  definido en `packages/core/src/orchestrator.py`.

## Estructura

```
src/
├── __init__.py
├── base.py        → Protocol MemoryBackend
├── sqlite.py      → Implementación SQLite (stub)
└── models.py      → Modelos SQLAlchemy
```
