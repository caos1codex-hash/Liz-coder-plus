# Memory Package

> Sistema de memoria persistente para Liz Coder Plus.

## Estado

**Sprint 1 - Prompt 3** — Memoria conversacional funcional con SQLite.

## Resumen

El paquete `memory` proporciona almacenamiento persistente para las
conversaciones de Liz Coder Plus. Cada mensaje (usuario, asistente,
sistema) se guarda en una base de datos SQLite, lo que permite que
las conversaciones sobrevivan reinicios del proceso.

## Arquitectura

```
Orchestrator
      │
      ▼
SessionManager (RAM cache)
      │
      ▼
MemoryManager (API pública)
      │
      ▼
ConversationRepository (acceso a datos)
      │
      ▼
DatabaseManager (conexión SQLite)
      │
      ▼
SQLite Database (conversations table)
```

### Componentes

| Componente | Archivo | Responsabilidad |
|---|---|---|
| **MemoryManager** | `src/memory/manager.py` | API pública del sistema de memoria. Gestiona inicialización, cache RAM y delega al repositorio. |
| **DatabaseManager** | `src/memory/database.py` | Gestiona la conexión SQLite (aiosqlite), ejecuta migraciones y expone métodos de bajo nivel. |
| **ConversationRepository** | `src/memory/repositories/conversation_repository.py` | Capa de acceso a datos para mensajes de conversación (CRUD asíncrono). |
| **ConversationMessage** | `src/memory/models/conversation.py` | Modelo de datos (dataclass) para un mensaje de conversación. |
| **initial.sql** | `src/memory/migrations/initial.sql` | Migración inicial que crea la tabla `conversations` e índices. |

### Flujo de datos

1. El usuario envía un mensaje por WebSocket.
2. El `Orchestrator` llama a `SessionManager.append_turn()`.
3. Si `MemoryManager` está adjuntado, el turn se persiste a SQLite.
4. El agente genera una respuesta.
5. La respuesta se guarda igualmente en RAM y SQLite.
6. En un reinicio, `SessionManager.restore_session()` recupera el historial desde SQLite.

### Base de datos SQLite

La tabla `conversations` tiene los siguientes campos:

| Campo | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER PK | Identificador auto-incremental. |
| `session_id` | TEXT | ID de la sesión a la que pertenece. |
| `role` | TEXT | `user`, `assistant` o `system`. |
| `content` | TEXT | Contenido del mensaje. |
| `created_at` | TEXT | Timestamp ISO 8601 (UTC). |
| `metadata` | TEXT | JSON con datos adicionales (agente, modo, etc.). |

La configuración de memoria se encuentra en `config/development.json`:

```json
{
  "memory": {
    "enabled": true,
    "provider": "sqlite",
    "path": "data/liz_memory.db",
    "max_history": 200
  }
}
```

## Estructura del paquete

```
packages/memory/
├── src/
│   ├── __init__.py           → Versión del paquete.
│   ├── base.py               → Protocol MemoryBackend (genérico).
│   ├── models.py             → Modelos SQLAlchemy (reservado para futuro).
│   ├── sqlite.py             → Implementación SQLAlchemy (reservado).
│   └── memory/               → Módulo de memoria conversacional.
│       ├── __init__.py
│       ├── manager.py        → MemoryManager (API pública).
│       ├── database.py       → DatabaseManager (SQLite/aiosqlite).
│       ├── models/
│       │   ├── __init__.py
│       │   └── conversation.py → ConversationMessage, MessageRole.
│       ├── repositories/
│       │   ├── __init__.py
│       │   └── conversation_repository.py → ConversationRepository.
│       └── migrations/
│           └── initial.sql   → Migración inicial.
├── tests/
│   └── __init__.py
└── pyproject.toml
```

## Uso básico

```python
from src.memory.manager import MemoryManager

# Inicializar
mm = MemoryManager(max_history=200)
await mm.initialize("data/liz_memory.db")

# Guardar mensajes
await mm.add_message("session-1", "user", "Hola Liz")
await mm.add_message("session-1", "assistant", "Hola, soy Liz")

# Recuperar contexto
context = await mm.get_context("session-1")

# Limpiar sesión
await mm.clear_session("session-1")

# Cerrar
await mm.close()
```

## Cache RAM

El `MemoryManager` mantiene un cache en memoria por sesión. En lecturas
sucesivas a `get_context()`, los datos se sirven desde RAM sin consultar
SQLite. El cache se actualiza automáticamente en cada `add_message()` y
se invalida con `clear_session()` o `_invalidate_cache()`.

## Extensiones futuras

- **Memoria a largo plazo:** hechos, preferencias y resúmenes entre sesiones.
- **Embeddings y búsqueda semántica:** para encontrar mensajes relevantes.
- **Vector database:** para RAG y recuperación contextual avanzada.
- **Múltiples backends:** soporte para PostgreSQL u otros motores.