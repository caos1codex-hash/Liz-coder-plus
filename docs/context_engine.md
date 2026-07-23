# Context Engineering Engine — Sprint 3.9

## Visión General

El Context Engineering Engine es el subsistema responsable de construir contextos
de agente inteligentes y eficientes en tokens. En lugar de enviar todo el historial,
archivos y memorias al agente, el motor selecciona, puntúa, ordena, presupuesta y
comprime solo la información más relevante para la tarea actual.

El problema que resuelve: los modelos de lenguaje tienen ventanas de contexto
finitas (típicamente 32K-128K tokens). Enviar información irrelevante reduce la
calidad de las respuestas y desperdicia tokens costosos.

**Propósito del motor:**

1. **Selección inteligente**: elegir solo archivos, memorias y turnos de historial
   relevantes para la tarea actual.
2. **Presupuesto de tokens**: respetar los límites del modelo con asignación
   proporcional entre archivos, memorias e historial.
3. **Compresión**: eliminar duplicados, comentarios innecesarios y truncar contenido
   de baja relevancia.
4. **Caché**: evitar reconstruir contextos idénticos con una caché LRU con TTL.
5. **Descomposición de tareas**: dividir tareas complejas en subtareas estructuradas
   con metadatos de ejecución.

## Componentes

### ContextEngine (`context_engine.py`)

Orquestador de nivel superior que coordina todos los subcomponentes del motor
de contexto. Es el punto de entrada único para todas las operaciones de contexto.

**`ContextEngineConfig`:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `max_context_tokens` | 32000 | Presupuesto total de tokens |
| `reserve_response` | 8192 | Tokens reservados para la respuesta del modelo |
| `compression_enabled` | True | Habilitar compresión |
| `ranking_enabled` | True | Habilitar re-ordenamiento |
| `history_limit` | 30 | Máximo de entradas de historial |
| `memory_limit` | 15 | Máximo de memorias |
| `file_limit` | 20 | Máximo de archivos |
| `context_cache_enabled` | True | Habilitar caché de contexto |
| `cache_ttl_seconds` | 300.0 | TTL de la caché (5 minutos) |
| `file_ratio` | 0.40 | Proporción de tokens para archivos |
| `memory_ratio` | 0.30 | Proporción de tokens para memorias |
| `history_ratio` | 0.30 | Proporción de tokens para historial |

**API Pública:**

```python
engine = ContextEngine(config=ContextEngineConfig(), event_bus=bus)

# Construir contexto completo
ctx = await engine.build_context(
    task="Fix login bug",
    agent_name="coder",
    session_id="s1",
    available_files=["src/auth/login.py", ...],
    file_contents={"src/auth/login.py": "...", ...},
    available_memories=[{"key": "api_patterns", ...}],
    history=[{"role": "user", "content": "..."}],
    system_prompt="...",
)

# Descomponer una tarea
breakdown = engine.decompose_task("Add login with Google OAuth")

# Generar subtareas enriquecidas
subtasks = engine.generate_subtasks("Add login with Google OAuth")

# Preparar contexto para el agente
merged = engine.prepare_agent_context("coder", task, context)

# Gestión de caché
engine.invalidate_cache(pattern="login")
stats = engine.cache_stats
```

### TaskDecomposer (`task_decomposer.py`)

Descompone tareas de alto nivel en subtareas estructuradas y priorizadas
usando reglas heurísticas. Diseñado para que una versión futura basada en LLM
pueda reemplazarlo sin cambiar la interfaz.

**6 patrones de descomposición:**

| Patrón | Keywords | Subtareas típicas | Agente sugerido |
|--------|----------|-------------------|-----------------|
| **Auth** | `login`, `auth`, `oauth`, `registro`, `autentic` | 6 pasos: investigar, configurar, backend, frontend, tests, docs | research → coder |
| **API** | `endpoint`, `api`, `ruta`, `route` | 5 pasos: investigar, diseñar, implementar, tests, docs | research → coder |
| **Database** | `database`, `migrat`, `schema`, `modelo`, `tabla` | 5 pasos: investigar, diseñar, migración, modelos, tests | research → coder |
| **Bug Fix** | `fix`, `corrige`, `error`, `bug`, `fallo`, `problema` | 5 pasos: investigar, causa raíz, fix, verificar, tests regresión | research → coder |
| **Feature** | `feature`, `funcionalidad`, `agrega`, `add`, `implementa` | 5 pasos: investigar, diseñar, implementar, tests, docs | research → coder |
| **Refactor** | `refactor`, `mejora`, `optimize`, `limpia` | 5 pasos: analizar, planificar, aplicar, ejecutar tests, corregir | research → coder |

**Fallback genérico**: cuando ningún patrón coincide, genera 3 subtareas:
`investigate → implement → verify`.

**Modelo de datos:**

- `SubTaskDecomposed`: nombre, descripción, prioridad (critical/high/normal/low),
  riesgo (high/medium/low), tokens estimados, dependencias, herramientas, agente.
- `TaskBreakdown`: tarea original + lista de subtareas + estrategia usada.

### SubTaskGenerator (`subtask_generator.py`)

Enriquece las subtareas del `TaskDecomposer` con metadatos de ejecución:
asignación de agente, sugerencias de herramientas, estimaciones de duración
y análisis de paralelizabilidad.

**`SubTaskMeta`:**

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `name` | `str` | Nombre único |
| `description` | `str` | Descripción de la subtarea |
| `suggested_agent` | `str` | Agente sugerido (research/coder/reviewer) |
| `suggested_tools` | `list[str]` | Herramientas sugeridas |
| `depends_on` | `list[str]` | Dependencias |
| `priority` | `SubTaskPriority` | Nivel de prioridad |
| `risk` | `SubTaskRisk` | Evaluación de riesgo |
| `estimated_tokens` | `int` | Presupuesto de tokens estimado |
| `estimated_duration_sec` | `float` | Duración estimada en segundos |
| `is_parallelizable` | `bool` | Puede ejecutarse en paralelo |

**Asignación heurística de agentes:**

| Prefijo del nombre | Agente asignado |
|--------------------|-----------------|
| `investigate`, `research`, `analyze` | `research` |
| `implement`, `create`, `design`, `fix`, `add` | `coder` |
| `verify`, `test`, `run_tests` | `reviewer` |
| `document`, `update_docs` | `coder` |

**Estimación de duración:** basada en una tabla de `(riesgo, prioridad) → segundos`.
Ejemplo: `high/critical = 120s`, `low/low = 5s`.

**Análisis de paralelizabilidad:** una subtarea es paralelizable si:
- No tiene dependencias, O
- Sus dependencias no son de riesgo alto ni prioridad crítica.

### FileSelector (`file_selector.py`)

Selecciona y puntúa archivos relevantes para una tarea usando cinco señales:

| Señal | Peso por defecto | Descripción |
|-------|------------------|-------------|
| **Keyword match** | 0.40 | Términos de la tarea encontrados en ruta o contenido |
| **Path heuristics** | 0.25 | Patrones tema-ruta (`auth → login.py`) |
| **Test proximity** | 0.15 | Archivos de test que coinciden con fuentes referenciadas |
| **Recency** | 0.10 | Archivos accedidos recientemente (decaimiento exponencial, τ=120s) |
| **Frequency** | 0.10 | Archivos accedidos frecuentemente (satura en 5 accesos) |

**Patrones tema-ruta predefinidos:**

- `auth`: login, logout, session, token, jwt, oauth, password, credential
- `database`: db, model, migration, schema, query, repository
- `api`: route, endpoint, controller, handler, rest, graphql
- `ui`: component, view, template, style, css, html, layout
- `test`: test, spec, mock, fixture, conftest
- `config`: config, settings, env, setup, pyproject, toml
- `deploy`: deploy, docker, ci, cd, pipeline, compose

**Salida:** `list[FileScore]` ordenado por score descendente, cada uno con
ruta, score, razones, tokens estimados, contenido y lenguaje detectado.

### MemorySelector (`memory_selector.py`)

Selecciona y puntúa entradas de memoria relevantes usando tres señales:

| Señal | Peso | Descripción |
|-------|------|-------------|
| **Keyword match** | 0.50 | Términos en key y value de la memoria |
| **Category relevance** | 0.35 | Categoría alineada con el dominio de la tarea |
| **Recency** | 0.15 | Memorias almacenadas recientemente (τ=7200s) |

**Categorías reconocidas:** `api`, `auth`, `database`, `ui`, `config`, `domain`, `testing`.

### HistorySelector (`history_selector.py`)

Selecciona turnos de conversación relevantes usando tres señales:

| Señal | Peso | Descripción |
|-------|------|-------------|
| **Keyword match** | 0.55 | Términos de la tarea en el contenido del mensaje |
| **Recency** | 0.30 | Mensajes recientes (decaimiento exponencial, τ=3600s) |
| **Position** | 0.15 | Mensajes cerca del final del historial |

**Ventana reciente:** los últimos N=5 mensajes reciben un bonus mínimo de
relevancia, garantizando contexto reciente incluso sin coincidencia de keywords.

### ContextRanker (`context_ranker.py`)

Motor de ranking unificado que re-puntúa y re-ordena items de cualquier fuente
(archivos, memorias, historial) usando una fórmula compuesta:

```
score = w_sim * similarity
      + w_dep * dependency_proximity
      + w_rec * recency_signal
      + w_freq * frequency_signal
      + w_div * diversity_boost
```

**Pesos configurables (`RankConfig`):**

| Peso | Valor | Descripción |
|------|-------|-------------|
| `weight_similarity` | 0.35 | Puntuación base del selector |
| `weight_dependency` | 0.20 | Proximidad a dependencias |
| `weight_recency` | 0.15 | Señal de recencia |
| `weight_frequency` | 0.15 | Señal de frecuencia |
| `weight_diversity` | 0.15 | Boost por diversidad de categoría |

**Diversity boosting:** previene que los top-N estén dominados por una sola
categoría. Items de categorías distintas reciben un boost de `0.3 * w_div`.
Items con 3+ vecinos de la misma categoría reciben una penalización.

### ContextBudgetManager (`context_budget.py`)

Gestiona el presupuesto de tokens para contextos de agente.

**`ContextBudgetConfig`:**

| Parámetro | Por defecto | Descripción |
|-----------|-------------|-------------|
| `max_tokens` | 32000 | Ventana de contexto total |
| `reserve_response` | 8192 | Reservado para respuesta del modelo |
| `system_budget` | 1024 | Reservado para system prompt |
| `file_ratio` | 0.40 | Proporción para archivos |
| `memory_ratio` | 0.30 | Proporción para memorias |
| `history_ratio` | 0.30 | Proporción para historial |
| `compression_threshold` | 0.80 | Ratio que dispara compresión |

**Cálculo de presupuesto disponible:**
```
disponible = max_tokens - reserve_response - system_budget
file_budget = disponible * file_ratio / total_ratio
memory_budget = disponible * memory_ratio / total_ratio
history_budget = disponible * history_ratio / total_ratio
```

**`enforce()`:** recorta items de cada categoría en orden inverso de score
(menos relevantes primero) hasta que cada categoría encaje en su presupuesto.

### ContextCompressor (`context_budget.py`)

Comprime el contexto cuando excede el presupuesto. Estrategias en orden:

1. **Eliminar duplicados:** hash del contenido para detectar items idénticos
   en archivos, memorias e historial.
2. **Strip de comentarios:** elimina comentarios de Python (`#`, `"""`), JS/TS
   (`//`, `/* */`), preservando strings que contengan `#`.
3. **Truncar baja relevancia:** items con `RelevanceLevel.NONE` o `LOW`
   se truncan al 50% de su longitud original.

### ContextCache (`context_cache.py`)

Caché en memoria con expiración TTL y evicción LRU para contextos ensamblados.

**Características:**

- **Clave de caché:** hash SHA-256 de `{task, agent, session}` normalizados.
- **TTL:** configurable (por defecto 300s / 5 minutos).
- **LRU:** evicción del menos recientemente usado al alcanzar `max_entries` (100).
- **Invalidación:** por clave individual, por patrón de substring, o total.
- **Estadísticas:** hits, misses, hit_rate, tamaño.

**API:**

```python
cache = ContextCache(ttl_seconds=300, max_entries=100, enabled=True)

key = cache.make_key(task="Fix login", agent="coder", session="s1")
ctx = cache.get(key)
cache.put(key, ctx)
cache.has(key)
cache.invalidate(key)
cache.invalidate_pattern("login")
count = cache.invalidate_all()
stats = cache.stats()
```

## Pipeline de Contexto

El `ContextBuilder` orquesta 7 pasos para ensamblar un `AgentContext` completo:

```
Entrada: task, agent_name, available_files, file_contents,
         available_memories, history, system_prompt
  │
  ▼
[1] FileSelector.select()
    Puntuar y seleccionar archivos relevantes por keywords, rutas,
    test proximity, recencia y frecuencia.
    → list[FileScore]
  │
  ▼
[2] MemorySelector.select()
    Puntuar y seleccionar memorias relevantes por keywords,
    categoría y recencia.
    → list[MemoryScore]
  │
  ▼
[3] HistorySelector.select()
    Puntuar y seleccionar turnos de historial relevantes por keywords,
    recencia y posición.
    → list[HistoryScore]
  │
  ▼
[4] Conversión a modelos compartidos
    FileScore → ContextFile
    MemoryScore → ContextMemory
    HistoryScore → ContextHistory
    Todos empaquetados en RankedContext.
    → RankedContext
  │
  ▼
[5] ContextRanker.rank_context()
    Re-puntuar todos los items con fórmula compuesta y
    aplicar diversity boosting.
    → RankedContext (reordenado)
  │
  ▼
[6] ContextBudgetManager.enforce()
    Recortar items por categoría hasta respetar el presupuesto
    de tokens. Se eliminan primero los de menor score.
    → RankedContext (dentro del presupuesto)
  │
  ▼
[7] ContextCompressor.compress_context()
    Si aún excede: eliminar duplicados, strip de comentarios,
    truncar baja relevancia.
    → AgentContext final
```

## Eventos del Motor de Contexto

| Evento | Descripción |
|--------|-------------|
| `context.engine.built` | Contexto construido (tokens, tiempos, ratios) |
| `context.engine.cache_hit` | Caché acertada (agente, sesión) |
| `context.engine.cache_miss` | Caché fallida (agente, sesión) |
| `context.engine.files_selected` | Archivos seleccionados (conteo, tokens, top file) |
| `context.engine.memories_selected` | Memorias seleccionadas (conteo, tokens) |
| `context.engine.history_selected` | Historial seleccionado (conteo, tokens) |
| `context.engine.compressed` | Contexto comprimido (ratio original vs comprimido) |
| `context.engine.task_decomposed` | Tarea descompuesta en subtareas |

## API Pública

### `ContextEngine`

```python
class ContextEngine:
    async def build_context(task, agent_name, session_id, *,
        available_files, file_contents, available_memories,
        history, system_prompt) -> AgentContext
    def prepare_agent_context(agent_name, task, context) -> dict
    def decompose_task(task, *, context) -> TaskBreakdown
    def generate_subtasks(task, *, context) -> list[SubTaskMeta]
    def invalidate_cache(pattern) -> int
    @property cache_stats -> dict
```

### `TaskDecomposer`

```python
class TaskDecomposer:
    def decompose(task, *, context) -> TaskBreakdown
```

### `SubTaskGenerator`

```python
class SubTaskGenerator:
    def generate(task, *, context) -> list[SubTaskMeta]
    def generate_breakdown(task, *, context) -> TaskBreakdown
    def to_planner_subtasks(metas) -> list[dict]
```

### `FileSelector`

```python
class FileSelector:
    def select(task, available_files, *, file_contents) -> list[FileScore]
    def record_access(path) -> None
    def to_context_files(scores) -> list[ContextFile]
```

### `MemorySelector`

```python
class MemorySelector:
    def select(task, available_memories) -> list[MemoryScore]
    def to_context_memories(scores) -> list[ContextMemory]
```

### `HistorySelector`

```python
class HistorySelector:
    def select(task, history) -> list[HistoryScore]
    def to_context_history(scores) -> list[ContextHistory]
```

### `ContextRanker`

```python
class ContextRanker:
    def rank(items) -> list[RankableItem]
    def rank_context(context) -> RankedContext
```

### `ContextBudgetManager`

```python
class ContextBudgetManager:
    def get_budget() -> ContextBudget
    def enforce(context) -> tuple[RankedContext, dict]
```

### `ContextCompressor`

```python
class ContextCompressor:
    def compress_context(context, target_tokens) -> tuple[RankedContext, dict]
```

### `ContextCache`

```python
class ContextCache:
    def make_key(*, task, agent, session, extra) -> str
    def get(key) -> AgentContext | None
    def put(key, context) -> None
    def has(key) -> bool
    def invalidate(key) -> bool
    def invalidate_pattern(pattern) -> int
    def invalidate_all() -> int
    def stats() -> dict
```