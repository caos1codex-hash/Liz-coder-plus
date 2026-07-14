# ContextEngine — Motor de Construcción de Contexto

> **Módulo:** `packages/multiagent/src/multiagent/memctx/context.py`
> **Versión:** 0.1.0
> **Última actualización:** Sprint 2.6

---

## 1. Introducción

El `ContextEngine` es el componente encargado de ensamblar contexto relevante a partir de los registros almacenados en el `MemoryEngine`. Su función principal es producir un `ContextSnapshot` —una instantánea lista para consumo— que un agente o workflow pueda utilizar directamente sin necesidad de procesamiento adicional.

El motor es independiente del almacenamiento: lee datos a través de `MemoryEngine` y no interactúa directamente con ningún backend de storage.

---

## 2. ContextRequest — Parámetros de solicitud

La clase `ContextRequest` define todos los parámetros que controlan cómo se construye el contexto:

```python
@dataclass
class ContextRequest:
    target: str = ""                          # Destino (nombre de agente, ID de workflow)
    owner: Optional[str] = None               # Filtrar por owner
    tags: Optional[List[str]] = None          # Tags requeridos (AND)
    memory_types: Optional[List[MemoryType]] = None  # Tipos de memoria a incluir
    max_tokens: int = 4096                    # Presupuesto máximo de tokens
    query: Optional[str] = None               # Consulta de texto para relevancia
    include_metadata: bool = True             # Incluir metadatos completos en el snapshot
    priority_threshold: int = 0               # Prioridad mínima para incluir
    max_records: int = 200                    # Máximo de registros a considerar (antes de recorte)
```

### Descripción detallada de cada parámetro

| Parámetro | Tipo | Default | Uso típico |
|-----------|------|---------|------------|
| `target` | `str` | `""` | Identifica quién consumirá el contexto. Se almacena en el snapshot para auditoría y se usa como tag. |
| `owner` | `Optional[str]` | `None` | Restringe los registros a los de un owner específico. `None` incluye todos. |
| `tags` | `Optional[List[str]]` | `None` | Lista de tags que **todos** deben estar presentes (lógica AND). |
| `memory_types` | `Optional[List[MemoryType]]` | `None` | Subconjunto de tipos a incluir. Por ejemplo, excluir `WORKFLOW` al construir contexto para un agente conversacional. |
| `max_tokens` | `int` | `4096` | Límite superior de tokens estimados para el snapshot final. Los registros se descartan en orden de menor prioridad hasta cumplir el presupuesto. |
| `query` | `Optional[str]` | `None` | Texto libre para filtrado de relevancia. Se pasa como parámetro `query` a `MemoryEngine.search()`. |
| `include_metadata` | `bool` | `True` | Si es `True`, cada registro del snapshot incluye campos completos (id, tipo, owner, tags, metadata, etc.). Si es `False`, solo incluye `id` y `content`. |
| `priority_threshold` | `int` | `0` | Registros con prioridad inferior a este valor se descartan antes del ordenamiento. Útil para excluir memorias de baja importancia. |
| `max_records` | `int` | `200` | Número máximo de registros candidatos a obtener de la memoria antes de aplicar deduplicación y recorte. |

---

## 3. Pipeline de construcción de contexto

El método `build_context(request)` ejecuta un pipeline de 6 pasos secuenciales:

```
ContextRequest
     │
     ▼
┌─────────────────────┐
│  1. FETCH           │  Obtener registros candidatos del MemoryEngine
│     (por tipo,      │  según memory_types, owner, tags, query
│      owner, tags)   │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  2. DEDUPLICATE     │  Eliminar registros con contenido idéntico
│     (hash MD5 de    │  usando hash del contenido serializado.
│      contenido)     │  Si hay duplicados, gana el de mayor prioridad.
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  3. FILTER          │  Descartar registros con
│     (prioridad)     │  priority < priority_threshold
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  4. SORT            │  Ordenar por (priority DESC, updated_at DESC)
│     (prioridad +    │  Los más importantes y recientes van primero.
│      recencia)      │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  5. TRIM            │  Recortar registros hasta cumplir
│     (presupuesto    │  el presupuesto de max_tokens.
│      de tokens)     │  Se descartan los últimos (menor prioridad).
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  6. SNAPSHOT        │  Ensamblar ContextSnapshot con metadata:
│                     │  - Lista de registros (con o sin metadata)
│                     │  - Token estimado total
│                     │  - Resumen textual
│                     │  - Flags de recorte
│                     │  - Lista de fuentes (owners únicos)
└─────────────────────┘
```

### Paso 1: Fetch

Si `memory_types` está definido, se realiza una búsqueda separada por cada tipo y se concatenan los resultados. Si no está definido, se realiza una sola búsqueda con los filtros disponibles. El límite de registros es `request.max_records` por consulta.

### Paso 2: Deduplicación

La deduplicación se basa en un hash MD5 del contenido serializado (`str(record.content)`). Cuando existen duplicados, se preserva el registro con mayor prioridad (los registros se ordenan por prioridad descendente antes de deduplicar).

```python
# Algoritmo simplificado:
records_sorted = sorted(records, key=lambda r: r.priority, reverse=True)
for rec in records_sorted:
    content_hash = hashlib.md5(str(rec.content).encode()).hexdigest()
    if content_hash not in seen:
        seen.add(content_hash)
        unique.append(rec)
    else:
        dup_count += 1
```

### Paso 3: Filtrado por prioridad

Se descartan todos los registros cuyo `priority` sea inferior a `priority_threshold`. Esto permite excluir memorias triviales o de baja confianza sin necesidad de eliminarlas del almacenamiento.

### Paso 4: Ordenamiento

Los registros se ordenan por una clave compuesta: `(priority DESC, updated_at DESC)`. Esto garantiza que los registros más importantes y más recientes aparezcan primero, y sean los últimos en ser descartados durante el recorte.

### Paso 5: Recorte por tokens

Se estima el consumo de tokens de cada registro acumulativamente. Cuando añadir el siguiente registro excedería el presupuesto (`max_tokens`), se detiene la iteración.

### Paso 6: Ensamblado del snapshot

Se construye el `ContextSnapshot` con todos los datos recopilados y se emite un evento `CONTEXT_CREATED`.

---

## 4. ContextSnapshot — Estructura del resultado

```python
@dataclass(slots=True)
class ContextSnapshot:
    id: str                        # UUID único del snapshot
    target: str                    # Destino del contexto
    records: List[Dict[str, Any]]  # Registros incluidos (serializados)
    summary: str                   # Resumen textual auto-generado
    total_tokens_estimate: int     # Tokens totales estimados
    max_tokens: int                # Presupuesto solicitado
    was_trimmed: bool              # Si se descartaron registros por presupuesto
    trimmed_count: int             # Cuántos registros se descartaron
    sources: List[str]             # Owners únicos incluidos
    created_at: str                # ISO-8601 UTC
    metadata: Dict[str, Any]       # Metadatos adicionales
```

### Resumen auto-generado

El campo `summary` se genera automáticamente con un formato como:

```
3 conversation, 2 knowledge, 1 project (from: architect, coder, system)
```

Esto permite a los agentes entender rápidamente la composición del contexto sin analizar cada registro.

---

## 5. Estimación de tokens

La estimación de tokens utiliza una heurística simple: **4 caracteres por token**. Esto se define mediante la constante `_CHARS_PER_TOKEN = 4`.

```python
def _estimate_tokens(record: MemoryRecord) -> int:
    content_str = str(record.content)
    return max(1, len(content_str) // 4)
```

**Nota:** Esta estimación es conservadora para texto en español (donde la relación promedio es ~3.5-4 caracteres/token) y puede diferir de la tokenización real del modelo LLM. Para presupuestos críticos, se recomienda aplicar un factor de seguridad del 80% al `max_tokens`.

---

## 6. ContextResult — Metadatos del proceso

Además del `ContextSnapshot`, el método `build_context()` devuelve un `ContextResult` con métricas del proceso de construcción:

```python
@dataclass
class ContextResult:
    snapshot: ContextSnapshot
    records_scanned: int      # Total de candidatos antes de filtrar
    duplicates_removed: int   # Duplicados eliminados en paso 2
    build_time_ms: float      # Tiempo total en milisegundos
```

---

## 7. Integración con MemoryEngine

El `ContextEngine` recibe una instancia de `MemoryEngine` en su constructor y utiliza su API de búsqueda para obtener los registros candidatos:

```python
ctx_engine = ContextEngine(
    memory_engine=engine,              # Obligatorio
    event_bus=engine.event_bus,        # Opcional, por defecto usa el del engine
)
```

El motor no realiza escrituras directamente al almacenamiento, pero puede:

- **Persistir contextos:** `save_context(snapshot)` almacena un snapshot como registro de tipo `CONTEXT`.
- **Cargar contextos:** `load_context(context_id)` recupera un snapshot previamente guardado.

---

## 8. Métodos de conveniencia

### 8.1 build_agent_context

Construye contexto optimizado para un agente específico:

```python
snapshot = await ctx_engine.build_agent_context(
    agent_name="coder",
    task={"tags": ["api", "rest"]},  # Tags adicionales de la tarea
    max_tokens=4096,
    tags=["proyecto-x"],             # Tags base
)
```

Internamente incluye los tipos `CONVERSATION`, `PROJECT` y `KNOWLEDGE`. Fusiona los tags de la tarea con los proporcionados.

### 8.2 build_workflow_context

Construye contexto para un workflow o paso específico:

```python
snapshot = await ctx_engine.build_workflow_context(
    workflow_id="wf-101",
    step_id="step-3",       # Opcional
    max_tokens=8192,        # Mayor presupuesto para workflows
)
```

Incluye los cuatro tipos de memoria principales (`WORKFLOW`, `CONVERSATION`, `PROJECT`, `KNOWLEDGE`) y agrega tags `["workflow", workflow_id, step_id]`.

### 8.3 Persistencia de contextos

```python
# Guardar un contexto para uso futuro
saved = await ctx_engine.save_context(snapshot)

# Cargarlo después
loaded = await ctx_engine.load_context(snapshot.id)
```

Los contextos persistidos se almacenan como registros de tipo `CONTEXT` con owner `"ContextEngine"` y tags `["context", target]`.

---

## 9. Casos de uso

### 9.1 Contexto para agente antes de ejecutar tarea

```python
# El workflow engine puede inyectar contexto antes de despachar
snapshot = await ctx_engine.build_agent_context(
    agent_name="reviewer",
    task={"description": "Revisar PR #42", "tags": ["pr-42", "python"]},
    max_tokens=4096,
)

# Inyectar en el payload del agente
task_payload["memory_context"] = snapshot.to_dict()
```

### 9.2 Contexto para workflow multi-paso

```python
# Antes de cada paso, cargar contexto acumulado del workflow
snapshot = await ctx_engine.build_workflow_context(
    workflow_id="wf-101",
    step_id="step-3",
    max_tokens=8192,
)

# El paso tiene acceso a resultados de pasos anteriores,
# decisiones de proyecto y conocimiento relevante.
```

### 9.3 Contexto personalizado

```python
from packages.multiagent.src.multiagent.memctx import ContextRequest, MemoryType

request = ContextRequest(
    target="mi-agente",
    owner="architect",
    tags=["proyecto-x", "v2"],
    memory_types=[MemoryType.PROJECT, MemoryType.KNOWLEDGE],
    max_tokens=2048,
    query="arquitectura",
    priority_threshold=3,
    include_metadata=False,  # Solo id y content
)

result = await ctx_engine.build_context(request)
print(f"Escaneados: {result.records_scanned}")
print(f"Duplicados: {result.duplicates_removed}")
print(f"Incluidos: {len(result.snapshot.records)}")
print(f"Recortados: {result.snapshot.trimmed_count}")
print(f"Tiempo: {result.build_time_ms:.1f}ms")
```

---

## 10. Eventos emitidos

El `ContextEngine` emite los siguientes eventos a través del `MemoryEventBus`:

| Evento | Payload clave | Cuándo |
|--------|--------------|--------|
| `CONTEXT_CREATED` | `target`, `records_included`, `records_scanned`, `duplicates_removed`, `tokens_estimate`, `was_trimmed`, `build_time_ms` | Al completar `build_context()` |
| `CONTEXT_LOADED` | `context_id`, `target` | Al guardar un contexto con `save_context()` |

Estos eventos permiten a otros componentes (observabilidad, plugins, adaptadores) reaccionar a las operaciones de construcción de contexto.