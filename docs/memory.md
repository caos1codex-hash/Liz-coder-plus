# Sistema de Memoria — Sprint 2.6

> **Módulo:** `packages/multiagent/src/multiagent/memctx/`
> **Versión:** 0.1.0
> **Última actualización:** Sprint 2.6

---

## 1. Visión general de la arquitectura

El sistema de memoria de Sprint 2.6 sigue una arquitectura en capas desacoplada donde cada componente tiene una responsabilidad única y bien definida. El diseño permite extender o reemplazar cualquier capa sin afectar al resto del sistema.

```
┌─────────────────────────────────────────────────┐
│                  Adaptadores                      │
│  WorkflowMemoryAdapter  RegistryMemoryAdapter   │
│              PluginMemoryBridge                   │
├─────────────────────────────────────────────────┤
│               ContextEngine                      │
│  (ensambla contexto para agentes/workflows)      │
├─────────────────────────────────────────────────┤
│              MemoryEngine                         │
│  (CRUD, búsqueda, eventos, métricas)             │
├──────────┬──────────┬──────────┬────────────────┤
│ Storage  │ Search   │ Events   │  Policies       │
│ (ABC)    │ Index    │ Bus      │  (TTL, evict)   │
│ InMemory │ (tags,   │ (7 tipos)│                  │
│ File     │  text,   │ (pub/sub)│                  │
│ Custom   │  meta)   │          │                  │
├──────────┴──────────┴──────────┴────────────────┤
│                  Métricas                        │
│  (latencia, conteos, percentiles)                │
└─────────────────────────────────────────────────┘
```

**Flujo de datos principal:**

1. El usuario o agente invoca `MemoryEngine.store()` o un método de conveniencia.
2. `MemoryEngine` delega la persistencia al backend de almacenamiento (`MemoryStorage`).
3. El registro se indexa en `MemorySearchIndex` para búsqueda rápida.
4. Se emite un evento `MEMORY_CREATED` a través de `MemoryEventBus`.
5. Se registran métricas de latencia y conteo en `MemoryMetrics`.
6. Los adaptadores (`WorkflowMemoryAdapter`, etc.) pueden reaccionar a los eventos.

---

## 2. Modelos de memoria

### 2.1 MemoryType (enum)

Define las categorías de memoria disponibles:

| Tipo | Valor | Descripción |
|------|-------|-------------|
| `CONVERSATION` | `"conversation"` | Turnos de diálogo y resúmenes |
| `PROJECT` | `"project"` | Decisiones, arquitectura y notas de proyecto |
| `WORKFLOW` | `"workflow"` | Contexto de ejecución de workflows y resultados |
| `KNOWLEDGE` | `"knowledge"` | Hechos aprendidos, patrones e insights |
| `CONTEXT` | `"context"` | Snapshots de contexto ensamblados |

### 2.2 MemoryRecord (registro base)

Es la clase fundamental de la que heredan todos los tipos especializados. Utiliza `@dataclass(slots=True)` para eficiencia de memoria.

**Campos principales:**

| Campo | Tipo | Default | Descripción |
|-------|------|---------|-------------|
| `id` | `str` | UUID auto-generado | Identificador único (máx. 128 chars) |
| `memory_type` | `MemoryType` | `KNOWLEDGE` | Categoría del registro |
| `content` | `Any` | `None` | Payload del dato (debe ser serializable a JSON) |
| `owner` | `str` | `"system"` | Entidad creadora (nombre de agente, sistema, etc.) |
| `tags` | `List[str]` | `[]` | Etiquetas buscables (cada una máx. 64 chars) |
| `metadata` | `Dict[str, Any]` | `{}` | Metadatos libres clave-valor |
| `version` | `int` | `1` | Versión para concurrencia optimista |
| `created_at` | `str` | ISO-8601 UTC | Timestamp de creación |
| `updated_at` | `str` | ISO-8601 UTC | Timestamp de última modificación |
| `ttl_seconds` | `Optional[int]` | `None` | Tiempo de vida en segundos (`None` = sin expiración) |
| `priority` | `int` | `0` | Prioridad numérica (mayre = más importante) |

**Validación automática en `__post_init__`:**
- IDs deben coincidir con el patrón `[a-zA-Z0-9._-]{1,128}`
- Tags deben coincidir con `[a-zA-Z0-9._-]{1,64}`
- Prioridad debe ser entero no negativo
- `ttl_seconds` debe ser entero positivo o `None`
- El contenido debe ser serializable a JSON

**Métodos clave:**
- `to_dict()` → serializa a diccionario compatible con JSON
- `from_dict(data)` → deserializa desde un diccionario
- `bump_version()` → incrementa versión y actualiza `updated_at`
- `is_expired()` → verifica si el registro excedió su TTL

### 2.3 ConversationMemory

Extiende `MemoryRecord` para diálogos:

```python
@dataclass(slots=True)
class ConversationMemory(MemoryRecord):
    session_id: str = ""       # Identificador de sesión
    role: str = "user"         # "user" o "assistant"
    summary: Optional[str] = None  # Resumen opcional del turno
```

Se fuerza `memory_type = CONVERSATION` en `__post_init__`. El contenido por defecto es `{"text": ""}`.

### 2.4 ProjectMemory

Para memorias de proyecto (decisiones, arquitectura, notas):

```python
@dataclass(slots=True)
class ProjectMemory(MemoryRecord):
    project_id: str = ""       # ID del proyecto
    category: str = "general"  # "decision", "architecture", "note", "file"
```

Se fuerza `memory_type = PROJECT`. El contenido por defecto es `{"description": ""}`.

### 2.5 WorkflowMemory

Para contexto de ejecución de workflows:

```python
@dataclass(slots=True)
class WorkflowMemory(MemoryRecord):
    workflow_id: str = ""      # ID del workflow
    step_id: str = ""          # ID del paso
    status: str = "pending"    # "pending", "running", "completed", "failed"
```

Se fuerza `memory_type = WORKFLOW`. El contenido por defecto es `{"result": None}`.

### 2.6 KnowledgeMemory

Para hechos aprendidos, patrones e insights:

```python
@dataclass(slots=True)
class KnowledgeMemory(MemoryRecord):
    confidence: float = 1.0    # Confianza entre 0.0 y 1.0
    source: str = ""           # Fuente del conocimiento
    knowledge_type: str = "fact"  # "fact", "pattern", "insight", "rule"
```

Se fuerza `memory_type = KNOWLEDGE`. Se valida que `confidence` esté en el rango `[0.0, 1.0]`.

---

## 3. API de almacenamiento (Storage)

### 3.1 MemoryStorage (ABC)

Clase base abstracta que define el contrato que todo backend debe implementar:

```python
class MemoryStorage(ABC):
    async def save(self, record: MemoryRecord) -> MemoryRecord: ...
    async def update(self, record: MemoryRecord) -> MemoryRecord: ...
    async def delete(self, memory_id: str) -> bool: ...
    async def get(self, memory_id: str) -> Optional[MemoryRecord]: ...
    async def exists(self, memory_id: str) -> bool: ...
    async def search(self, query, tags, owner, memory_type, metadata_filter, limit, offset) -> List[MemoryRecord]: ...
    async def list(self, owner, memory_type, limit, offset) -> List[MemoryRecord]: ...
    async def clear(self, owner) -> int: ...
    async def count(self, owner) -> int: ...
```

### 3.2 InMemoryStorage

Backend basado en diccionario Python, seguro para hilos mediante `threading.Lock`. Ideal para pruebas, sesiones efímeras y desarrollo.

**Características:**
- Detección de duplicados en `save()`
- Control de versiones optimista en `update()`
- Búsqueda con filtros AND (owner, tipo, tags, metadata, query de texto)
- Ordenamiento por prioridad descendente, luego `updated_at` descendente

### 3.3 FileStorage

Backend persistente basado en archivos JSON. Cada registro se almacena como un archivo individual.

**Estructura de directorios:**
```
.memory/
├── conversation/
│   ├── <uuid>.json
│   └── ...
├── project/
├── workflow/
├── knowledge/
└── context/
```

**Características:**
- Índice en memoria (`id → ruta relativa`) reconstruido al inicio
- Control de versiones optimista
- Tolerancia a archivos corruptos (se registran advertencias, no se interrumpe la búsqueda)
- `clear()` elimina archivos físicamente del disco

### 3.4 Cómo añadir un nuevo backend

Para implementar un backend personalizado (ej. SQLite, PostgreSQL, Redis):

1. Crear una clase que herede de `MemoryStorage`
2. Implementar los 9 métodos abstractos
3. Garantizar seguridad de hilos
4. Pasar la instancia al `MemoryEngine`:

```python
class SQLiteStorage(MemoryStorage):
    async def save(self, record: MemoryRecord) -> MemoryRecord:
        # Implementación con sqlite3 o aiosqlite
        ...

engine = MemoryEngine(storage=SQLiteStorage("mem.db"))
```

Véase [storage-backends.md](storage-backends.md) para una guía detallada.

---

## 4. MemoryEngine — Operaciones CRUD

`MemoryEngine` es la interfaz principal del sistema. Gestiona el ciclo de vida completo de los registros.

### 4.1 Ciclo de vida

```python
engine = MemoryEngine()          # Crea con InMemoryStorage por defecto
await engine.start()             # Carga IDs existentes, reconstruye índices
# ... operaciones CRUD ...
await engine.stop()              # Limpia cachés
```

### 4.2 CRUD básico

```python
# Crear
record = MemoryRecord(content="Hola mundo", tags=["saludo"])
saved = await engine.store(record)

# Leer
retrieved = await engine.retrieve(saved.id)  # MemoryRecord o None

# Actualizar (control de versiones)
retrieved.content = "Hola mundo actualizado"
updated = await engine.update(retrieved)  # version se incrementa automáticamente

# Eliminar
deleted = await engine.remove(saved.id)  # True si existía
```

### 4.3 Operaciones masivas

```python
# Almacenar múltiples registros
results = await engine.store_many([rec1, rec2, rec3])

# Eliminar múltiples
count = await engine.remove_many(["id1", "id2", "id3"])

# Limpiar todo o por owner
count = await engine.clear()            # Elimina todo
count = await engine.clear(owner="agente-coder")  # Solo del owner
```

### 4.4 Búsqueda

```python
# Búsqueda combinada
results = await engine.search(
    query="arquitectura",           # Búsqueda de texto (substring)
    tags=["proyecto", "v2"],        # Todos los tags deben coincidir (AND)
    owner="planner",                # Filtrar por owner
    memory_type=MemoryType.PROJECT, # Filtrar por tipo
    metadata_filter={"status": "active"},  # Filtro de metadata
    limit=20,
    offset=0,
)
```

### 4.5 Métodos de conveniencia

El motor ofrece métodos atajos para crear registros especializados sin instanciar manualmente los subtipos:

```python
# Conversación
await engine.store_conversation(
    session_id="sess-42",
    role="user",
    text="¿Cómo implemento autenticación JWT?",
    owner="user",
    tags=["auth", "jwt"],
)

# Proyecto
await engine.store_project(
    project_id="api-gateway",
    description="Usar FastAPI con middleware de auth",
    category="decision",
    owner="architect",
)

# Workflow
await engine.store_workflow(
    workflow_id="wf-101",
    step_id="step-3",
    result={"output": "archivo_generado.py"},
    status="completed",
    agent_name="coder",
)

# Conocimiento
await engine.store_knowledge(
    text="Python 3.11+ soporta match/case",
    source="documentación oficial",
    confidence=0.95,
    knowledge_type="fact",
    tags=["python", "syntax"],
)
```

### 4.6 Health check

```python
health = await engine.health_check()
# {
#   "status": "healthy",
#   "total_records": 42,
#   "indexed_records": 42,
#   "metrics": { ... }
# }
```

---

## 5. ContextEngine

El `ContextEngine` ensambla contextos relevantes a partir de la memoria para que los agentes y workflows los consuman. Produce un `ContextSnapshot` con los registros más relevantes, deduplicados y recortados al presupuesto de tokens.

Véase [context-engine.md](context-engine.md) para la documentación completa.

---

## 6. Índice de búsqueda

`MemorySearchIndex` mantiene índices invertidos en memoria para búsqueda eficiente:

| Índice | Tipo | Clave | Valor |
|--------|------|-------|-------|
| `_records` | Primario | `id` | `MemoryRecord` |
| `_by_tag` | Invertido | `tag` | `Set[id]` |
| `_by_owner` | Invertido | `owner` | `Set[id]` |
| `_by_type` | Invertido | `MemoryType` | `Set[id]` |
| `_by_word` | Invertido | `palabra minúscula` | `Set[id]` |
| `_by_meta_key` | Invertido | `clave metadata` | `Set[id]` |
| `_by_meta_kv` | Invertido | `clave:valor` | `Set[id]` |

### Búsqueda combinada

El método `combined_search()` interseca los resultados de múltiples filtros y asigna puntuaciones:

```python
results = search_index.combined_search(
    query="autenticación",
    tags=["seguridad"],
    owner="architect",
    memory_type=MemoryType.KNOWLEDGE,
    metadata_filter={"source": "docs"},
    limit=10,
)
# Cada resultado es un SearchResult con:
# - record: MemoryRecord
# - score: float (palabras coincidentes + bonus de prioridad)
# - matched_by: List[str] (criterios que coincidieron)
```

**Tokenización:** Se extraen palabras alfanuméricas con la regex `[a-zA-Z0-9_]+` y se convierten a minúsculas. Se indexan tanto el contenido como los tags y el owner.

---

## 7. Sistema de políticas

Las políticas gobiernan el ciclo de vida de los registros de memoria mediante reglas configurables.

### 7.1 MemoryPolicy

```python
@dataclass
class MemoryPolicy:
    name: str                    # Nombre único de la política
    description: str = ""
    memory_types: List[MemoryType] = []  # Vacío = aplica a todos
    owners: List[str] = []       # Vacío = aplica a todos
    ttl_seconds: Optional[int] = None    # Tiempo de vida
    max_records: Optional[int] = None    # Máximo por owner/tipo
    max_total_records: Optional[int] = None  # Máximo global
    priority_floor: int = 0      # Piso de prioridad para eviction
    action: PolicyAction = EXPIRE  # EXPIRE, ARCHIVE, COMPRESS, EVICT
    enabled: bool = True
```

### 7.2 Acciones disponibles

| Acción | Comportamiento |
|--------|---------------|
| `EXPIRE` | Elimina registros expirados |
| `ARCHIVE` | Marca como archivados en metadata |
| `COMPRESS` | Preparado para compresión futura |
| `EVICT` | Elimina registros de baja prioridad |

### 7.3 MemoryPolicyManager

```python
mgr = MemoryPolicyManager()

# Registrar políticas
mgr.add_policy(MemoryPolicy(
    name="expirar-conversaciones",
    memory_types=[MemoryType.CONVERSATION],
    ttl_seconds=3600,  # 1 hora
    action=PolicyAction.EXPIRE,
))

mgr.add_policy(MemoryPolicy(
    name="evictar-baja-prioridad",
    max_total_records=1000,
    priority_floor=1,
    action=PolicyAction.EVICT,
))

# Ejecutar limpieza
results = await mgr.cleanup(engine)
# {"expired": 5, "archived": 2, "evicted": 3, "total_cleaned": 10}

# Forzar límites
results = await mgr.enforce_limits(engine)
# {"evicted_agent-1": 3, "global_evicted": 5}
```

---

## 8. Sistema de eventos

### 8.1 Tipos de eventos (7)

| Evento | Cuándo se emite |
|--------|----------------|
| `MEMORY_CREATED` | Al almacenar un nuevo registro |
| `MEMORY_UPDATED` | Al actualizar un registro existente |
| `MEMORY_DELETED` | Al eliminar un registro |
| `CONTEXT_CREATED` | Al construir un contexto nuevo |
| `CONTEXT_LOADED` | Al cargar un contexto persistido |
| `CONTEXT_TRIMMED` | Cuando el contexto excede el presupuesto de tokens |
| `CONTEXT_EXPIRED` | Cuando un contexto expira |

### 8.2 MemoryEventBus

```python
bus = MemoryEventBus(max_history=500)

# Suscribir a un tipo específico
token = await bus.subscribe(MemoryEventType.MEMORY_CREATED, mi_handler)

# Suscribir a todos los eventos
token = await bus.subscribe_all(mi_handler_global)

# Cancelar suscripción
await bus.unsubscribe(token)

# Consultar historial
history = await bus.history(event_type=MemoryEventType.MEMORY_CREATED, limit=20)

# Métricas del bus
metrics = bus.metrics()
# {"published": 42, "delivered": 38, "errors": 0, "by_type": {...}, ...}
```

### 8.3 Estructura de MemoryEvent

```python
@dataclass
class MemoryEvent:
    type: MemoryEventType | str
    source: str             # Componente emisor
    payload: Dict[str, Any] # Datos del evento
    id: str                 # UUID único
    timestamp: str          # ISO-8601 UTC
    correlation_id: Optional[str] = None  # Para tracing distribuido
```

---

## 9. Adaptadores de integración

### 9.1 WorkflowMemoryAdapter

Conecta `MemoryEngine` con el `WorkflowEngine`:

```python
adapter = WorkflowMemoryAdapter(memory_engine)

# Guardar contexto antes de ejecutar
await adapter.save_workflow_context("wf-101", {"objective": "Generar API"})

# Restaurar contexto
records = await adapter.load_workflow_context("wf-101")

# Registrar resultado de paso
await adapter.record_step_result("wf-101", "step-3", {"file": "output.py"})

# Registrar resultado final (prioridad 10)
await adapter.record_workflow_result("wf-101", {"status": "success"})

# Obtener historial completo
history = await adapter.get_workflow_history("wf-101")
```

### 9.2 RegistryMemoryAdapter

Conecta `MemoryEngine` con el `AgentRegistry`:

```python
adapter = RegistryMemoryAdapter(memory_engine)

# Registrar agente para acceso a memoria
adapter.register_agent_memory("coder")
adapter.register_agent_memory("reviewer", owner="code-review-team")

# Almacenar en nombre del agente
await adapter.store_agent_memory("coder", {"prefiere_tabs": True}, tags=["preferencia"])

# Recuperar memoria del agente
records = await adapter.get_agent_memory("coder")

# Obtener contexto para inyección en tarea
context = await adapter.get_agent_context("coder", task={"tags": ["python"]})
```

### 9.3 PluginMemoryBridge

Puentea eventos de memoria al `PluginEventBus`:

```python
bridge = PluginMemoryBridge(memory_event_bus, plugin_event_bus)
await bridge.connect()    # Suscribe a todos los eventos de memoria

# Los plugins reciben eventos como:
# {"type": "memory.memory.created", "source": "MemoryEngine", "payload": {...}}

await bridge.disconnect()  # Limpia suscripciones
```

---

## 10. Métricas

`MemoryMetrics` recopila estadísticas operativas con trackers de latencia basados en muestras:

```python
metrics = engine.metrics
snapshot = metrics.snapshot()
```

**Métricas disponibles:**

| Categoría | Métricas |
|-----------|----------|
| Operaciones | `store`, `update`, `delete`, `retrieve`, `search` (conteos) |
| Hit rate | Tasa de acierto de `retrieve` |
| Latencia | `avg`, `p50`, `p95`, `p99`, `max` para store/retrieve/search |
| Por tipo | Desglose de stores por `MemoryType` |
| Context | `builds`, `trims`, `avg_tokens` |
| Búsqueda | `total_results`, `avg_results` |

Los trackers de latencia mantienen hasta 1000 muestras en un `deque` con ventanas deslizantes.

---

## 11. Ejemplos de uso

### 11.1 Memoria de conversación completa

```python
from packages.multiagent.src.multiagent.memctx import MemoryEngine

engine = MemoryEngine()
await engine.start()

# Guardar turnos de conversación
await engine.store_conversation("sess-1", "user", "¿Cómo creo un endpoint REST?")
await engine.store_conversation(
    "sess-1", "assistant",
    "Puedes usar FastAPI con @app.get('/items/{item_id}')",
    owner="coder-agent",
    tags=["fastapi", "rest"],
    metadata={"model": "gpt-4"},
)

# Buscar conversaciones previas
prev = await engine.search(
    tags=["sess-1"],
    memory_type=MemoryType.CONVERSATION,
)
```

### 11.2 Conocimiento acumulado

```python
# Almacenar aprendizajes
await engine.store_knowledge(
    text="Este proyecto usa PostgreSQL 16 con pgvector para embeddings",
    source="config",
    confidence=1.0,
    knowledge_type="fact",
    tags=["database", "postgres"],
    priority=5,
)

await engine.store_knowledge(
    text="El equipo prefiere composición sobre herencia",
    source="code-review",
    confidence=0.85,
    knowledge_type="pattern",
    tags=["architecture", "pattern"],
)
```

### 11.3 Contexto para un agente

```python
from packages.multiagent.src.multiagent.memctx import ContextEngine

ctx_engine = ContextEngine(engine)

# Contexto para el agente "coder"
snapshot = await ctx_engine.build_agent_context(
    agent_name="coder",
    task={"tags": ["api", "fastapi"]},
    max_tokens=4096,
)

print(f"Registros incluidos: {len(snapshot.records)}")
print(f"Tokens estimados: {snapshot.total_tokens_estimate}")
print(f"¿Recortado?: {snapshot.was_trimmed}")
```

### 11.4 Política de limpieza automática

```python
from packages.multiagent.src.multiagent.memctx import MemoryPolicyManager, MemoryPolicy, PolicyAction

mgr = MemoryPolicyManager()
mgr.add_policy(MemoryPolicy(
    name="limpiar-sesiones",
    memory_types=[MemoryType.CONVERSATION],
    ttl_seconds=86400,  # 24 horas
    action=PolicyAction.EXPIRE,
))
mgr.add_policy(MemoryPolicy(
    name="limitar-memoria",
    max_total_records=5000,
    priority_floor=1,
    action=PolicyAction.EVICT,
))

# Ejecutar limpieza (se puede programar con asyncio)
results = await mgr.cleanup(engine)
print(f"Registros limpiados: {results['total_cleaned']}")
```