# Sprint 1.4 — Audit Report

## Estado Actual

- **Rama:** main
- **Commits:** 11 (desde la fundación hasta Sprint 1 Prompt 3)
- **Tests:** 78 pasando
- **Working tree:** limpio

## Resumen de Hallazgos

| Severidad | Cantidad |
|-----------|----------|
| Críticos  | 3        |
| Altos     | 3        |
| Medios    | 17       |
| Bajos     | 19       |
| **Total** | **42**   |

## Problemas Críticos

1. **AppConfig no parsea secciones logging/backend/memory** — La configuración de producción (host, port, CORS, database path) es silenciosamente ignorada.
2. **MemoryManager nunca se conecta al Orchestrator** — `attach_memory()` nunca es llamado en ningún code path activo. Toda conversación es efímera a pesar del código de persistencia.
3. **Import path collision** — `TYPE_CHECKING` imports en orchestrator y session_manager apuntan a rutas que no existen bajo `packages/core/src/`.

## Código Muerto Identificado

- `apps/backend/src/core/orchestrator.py` — stub completo
- `packages/memory/src/sqlite.py` — reemplazado por `memory/database.py`
- `packages/memory/src/models.py` — modelos SQLAlchemy sin uso
- `packages/memory/src/base.py` — Protocol sin consumidores
- `packages/shared/src/models.py` — 6 modelos sin uso
- `packages/shared/src/ws_models.py` — modelos sin uso
- `packages/shared/src/utils.py` — funciones sin uso
- `packages/shared/src/types.py` — tipos sin uso
- `packages/shared/src/enums.py` — enums sin consumidores externos
- `apps/backend/src/events/bus.py` — EventBus sin instanciar
- Paquetes completos muertos: `packages/agents/`, `packages/tools/`

## Duplicaciones

- `PermissionMode` definido en shared Y en backend
- `AgentRole` (shared) vs `MessageRole` (memory)
- `AgentRegistry` (agents) vs `AgentRouter` (core)
- `ToolRegistry` (tools) vs `self._tools` dict (orchestrator)

## Plan de Corrección

### Fase 2: Memory
- Eliminar asserts por validaciones con RuntimeError
- Agregar manejo de errores en DatabaseManager
- Mejorar tipado en ConversationRepository
- Agregar validación de roles en repository

### Fase 3: Integración
- Conectar MemoryManager al startup del backend
- Actualizar AppConfig para incluir secciones faltantes
- Usar WebSocketChatRequest en ws_routes.py

### Fase 4: Calidad
- Marcar código muerto con docstrings claros
- Corregir versión en AppConfig
- Mejorar error handling en orchestrator y session_manager

### Fase 5: Tests
- Tests de error handling (DB caída, datos inválidos)
- Tests de integración startup con memory
- Tests de validación de entradas

### Fase 6: Docs
- Actualizar arquitectura con hallazgos
- Documentar código muerto y razones
- Guía de extensión del sistema memory