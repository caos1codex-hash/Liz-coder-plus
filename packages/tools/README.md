# Tools Package

> Herramientas externas para Liz Coder Plus.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto. Implementación real en Sprint 3.

## Diseño

- Cada herramienta es una clase que cumple el Protocol `Tool` definido en
  `packages/core/src/orchestrator.py`.
- Antes de ejecutarse, toda herramienta pasa por el `PermissionService`
  que decide si debe confirmarse con el usuario o ejecutarse directamente.
- Tipos previstos de herramientas:
  - **Shell:** ejecución de comandos del sistema.
  - **File:** lectura / escritura de archivos.
  - **Web:** búsqueda y descarga de información.
  - **App:** automatización de aplicaciones (futuro).

## Estructura

```
src/
├── __init__.py
├── base.py        → Clase base BaseTool
└── registry.py    → Registro de herramientas disponibles
```
