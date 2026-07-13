# Agents Package

> Sistema de agentes especializados para Liz Coder Plus.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto. Implementación real en Sprint 2/5.

## Diseño

- Cada agente es una clase que cumple el Protocol `Agent` definido en
  `packages/core/src/orchestrator.py`.
- Los agentes se registran en el orquestador, que decide cuál invocar.
- Tipos previstos de agentes:
  - **Conversacional:** diálogo general.
  - **Código:** ayuda con programación.
  - **Sistema:** control del PC y herramientas.
  - **Investigación:** búsqueda y síntesis.

## Estructura

```
src/
├── __init__.py
├── base.py        → Clase base BaseAgent
└── registry.py    → Registro de agentes disponibles
```
