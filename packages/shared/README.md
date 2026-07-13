# Shared Package

> Modelos, tipos y utilidades compartidas entre todos los paquetes.

## Estado

**Sprint 1 - Prompt 1 (Foundation)** — Esqueleto.

## Contenido

- **`models.py`:** modelos Pydantic compartidos (mensajes, eventos, etc.).
- **`types.py`:** aliases y enumeraciones comunes.
- **`enums.py`:** enumeraciones (PermissionMode, AgentRole, etc.).
- **`utils.py`:** utilidades varias (logging, ids, timestamps).

## Principio

Este paquete **no debe depender** de ningún otro paquete del proyecto.
Puede ser importado por todos los demás sin crear ciclos.
