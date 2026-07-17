# Tool Intelligence — `packages/tool_intelligence`

> Sprint 4.1 — Tool Intelligence Foundation
>
> Capa de razonamiento sobre herramientas que permite a Liz Coder
> **descubrir, clasificar y seleccionar** automáticamente las herramientas
> disponibles antes de ejecutar una tarea, de forma similar a Claude Code
> y Codex.

## 1. Visión general

El paquete `tool_intelligence` añade una capa de inteligencia sobre el
`ToolRegistry` existente (`packages/tools`). En lugar de ejecutar
herramientas a ciegas, el orquestador puede consultar a esta capa para
saber **cuál** herramienta es la más adecuada para una intención de
usuario, un plan o un contexto de ejecución.

La capa es **estática y declarativa**: opera sobre metadatos
(`ToolMetadata`) sin instanciar las herramientas. Esto permite indexar,
rankear y filtrar cientos de herramientas sin retener objetos vivos en
memoria.

### Flujo de selección

```
┌──────────────────┐
│  ToolRegistry    │  (paquete existente — instancias vivas)
│  (packages/tools)│
└────────┬─────────┘
         │  ToolMetadata.from_base_tool(tool)
         ▼
┌──────────────────┐
│ CapabilityIndex  │  Catálogo buscable por id, nombre, alias,
│                  │  categoría, capability, tag, keyword.
└────────┬─────────┘
         │  candidates = index.by_capability(...) + by_keyword(...)
         ▼
┌──────────────────┐
│ Compatibility    │  Filtra por OS, dependencias, plugins,
│ Checker          │  permisos y modelo requerido.
└────────┬─────────┘
         │  compatible_candidates = [m for m if checker.check(m, ctx)]
         ▼
┌──────────────────┐
│ Tool Ranker      │  Scorea con 6 factores ponderados:
│                  │  semántica, capability, historial, coste,
│                  │  latencia, confiabilidad.
└────────┬─────────┘
         │  ranked = ranker.rank(compatible, intent, ctx)
         ▼
┌──────────────────┐
│ Tool Selector    │  Orquesta el flujo completo y devuelve
│                  │  SelectionResult con los Top-K candidatos.
└──────────────────┘
```

Cada componente es independiente y testeable por separado. El
`ToolSelector` es la API pública de alto nivel: la usa el orquestador
para obtener candidatos ordenados por score.

## 2. Estructura del paquete

```
packages/tool_intelligence/
├── __init__.py            # API pública
├── exceptions.py          # ToolIntelligenceError + 4 subclases
├── models.py              # ToolMetadata, UserIntent, SelectionContext, ...
├── capability_index.py    # CapabilityIndex + extract_keywords()
├── registry_cache.py      # RegistryCache (TTL, invalidate, refresh, incremental)
├── compatibility.py       # CompatibilityChecker + binary_dependency_resolver
├── tool_ranker.py         # ToolRanker + RankerWeights
└── tool_selector.py       # ToolSelector, SelectionResult, SubTaskSelection
```

Todos los archivos están documentados con docstrings al nivel de
módulo, clase y método. El paquete no depende de `packages/tools` ni
de `packages/core` (puede usarse standalone).

## 3. Componentes

### 3.1 `ToolMetadata` (`models.py`)

Snapshot estático de una herramienta. Cada herramienta expone:

| Campo                    | Tipo            | Descripción                                     |
|--------------------------|-----------------|-------------------------------------------------|
| `id`                     | `str`           | Identificador estable entre ejecuciones.        |
| `name`                   | `str`           | Nombre único en el registro.                    |
| `description`            | `str`           | Descripción de 1-3 frases.                      |
| `category`               | `ToolCategory`  | SHELL / FILE / WEB / APP / SYSTEM / LLM / ...   |
| `capabilities`           | `list[str]`     | Capabilities jerárquicas (`filesystem.read`).   |
| `input_schema`           | `dict`          | Esquema tipo JSON-schema de entradas.           |
| `output_schema`          | `dict`          | Esquema tipo JSON-schema de salidas.            |
| `risk_level`             | `RiskLevel`     | LOW / MEDIUM / HIGH / CRITICAL.                 |
| `estimated_cost`         | `float`         | Coste relativo en `[0.0, 1.0]`.                 |
| `estimated_latency`      | `float`         | Latencia esperada en milisegundos.              |
| `requires_confirmation`  | `bool`          | Si el usuario debe confirmar antes de ejecutar. |
| `supports_parallel`      | `bool`          | Si puede ejecutarse en paralelo con otras.      |
| `supported_platforms`    | `list[str]`     | Plataformas soportadas (linux/macos/windows).   |
| `aliases`                | `list[str]`     | Nombres alternativos para fuzzy lookup.         |
| `tags`                   | `list[str]`     | Etiquetas libres para filtrado.                 |
| `keywords`               | `list[str]`     | Keywords extraídas de la descripción.           |
| `version`                | `str`           | Versión semántica.                              |
| `permissions`            | `list[str]`     | Permisos requeridos (`fs:write`).               |
| `dependencies`           | `list[str]`     | Binarios o paquetes externos necesarios.        |
| `required_model`         | `str \| None`   | Modelo LLM requerido, si aplica.                |
| `plugins`                | `list[str]`     | Plugins que deben estar cargados.               |
| `reliability`            | `float`         | Confiabilidad declarada en `[0.0, 1.0]`.        |
| `metadata`               | `dict`          | Extensión libre.                                |

El factory `ToolMetadata.from_base_tool(tool)` construye un metadata a
partir de cualquier objeto `BaseTool` del paquete existente
`packages/tools`, mapeando `permission_level → RiskLevel` y
`category → ToolCategory`.

### 3.2 `CapabilityIndex` (`capability_index.py`)

Catálogo en memoria con índices invertidos para búsqueda O(1) por
cualquier dimensión:

- Por `id` (exacto).
- Por `name` (exacto) o `alias` (case-insensitive).
- Por `category` (agrupación).
- Por `capability` — jerárquico: `"filesystem"` encuentra
  `"filesystem.read"` y `"filesystem.write"`. Wildcards: `"filesystem.*"`.
- Por `tag` (case-insensitive).
- Por `keyword` (case-insensitive).

Operaciones principales:

- `add(metadata)` / `update(metadata)` / `remove(id)` / `clear()`
- `get(id)` / `get_by_name(name)`
- `by_category(cat)` / `by_capability(cap)` / `by_tag(t)` / `by_keyword(kw)`
- `search(query)` — texto libre, tokenizado y scoreado.
- `find(category=..., capabilities=..., tags=..., keywords=..., max_risk_level=...)`
  — filtro AND multi-criterio.
- `summary()` — métricas del índice (total, por categoría, por riesgo,
  hits/misses).

La función auxiliar `extract_keywords(text)` tokeniza descripciones a
keywords normalizados, eliminando stopwords en inglés.

### 3.3 `RegistryCache` (`registry_cache.py`)

Envoltorio sobre `CapabilityIndex` que añade:

- **Lazy build**: el índice se construye en el primer `get()`.
- **TTL**: opcional (`ttl_seconds`). Si el índice es más antiguo que el
  TTL, se reconstruye automáticamente en el siguiente `get()`.
- **`invalidate()`**: marca el índice como sucio; se reconstruye en el
  siguiente `get()`.
- **`refresh()`**: reconstruye inmediatamente y devuelve el nuevo índice.
- **`upsert(metadata)`**: inserta o actualiza una herramienta sin
  reconstruir todo el índice.
- **`incremental_update(added=..., updated=..., removed=...)`**: aplica
  un batch de cambios en una sola llamada.
- **`stats()`**: reporta builds, invalidaciones, upserts, removes y
  último error.

El `loader` es un callable `() -> Iterable[ToolMetadata]` que el cache
invoca cada vez que necesita reconstruir. Típicamente recorre el
`ToolRegistry` existente y convierte cada tool con
`ToolMetadata.from_base_tool`.

### 3.4 `CompatibilityChecker` (`compatibility.py`)

Verifica que una herramienta puede ejecutarse en el entorno actual.
Cinco chequeos independientes:

| Check            | Origen de la restricción                    |
|------------------|---------------------------------------------|
| `platform`       | `metadata.supported_platforms` vs `ctx.platform`. |
| `dependencies`   | `metadata.dependencies` resuelto por un `DependencyResolver`. Por defecto `shutil.which`. |
| `plugins`        | `metadata.plugins` ⊆ `ctx.loaded_plugins`. |
| `permissions`    | `metadata.permissions` ⊆ `ctx.granted_permissions`. |
| `required_model` | `metadata.required_model` ∈ `ctx.available_models`. |

API:

- `check(metadata, ctx, use_cache=True) -> CompatibilityReport`
- `check_or_raise(metadata, ctx)` — lanza `ToolNotCompatible` si falla.
- `filter_compatible(tools, ctx) -> list[(metadata, report)]`
- `clear_cache()` — vacía la caché interna.

Los reports se cachean por `(tool_id, platform, plugins, perms, models)`
para evitar re-chequear en bucles cerrados.

### 3.5 `ToolRanker` (`tool_ranker.py`)

Scorea y ordena candidatos. Seis factores ponderados, cada uno
normalizado a `[0.0, 1.0]`:

| Factor         | Origen                                                  | Peso default |
|----------------|---------------------------------------------------------|--------------|
| `semantic`     | Overlap de keywords entre `UserIntent` y `ToolMetadata`.| 0.25         |
| `capability`   | 1.0 exacto / 0.8 padre / 0.6 sub-capability / 0.0 none. | 0.25         |
| `history`      | `ToolHistoryStats.success_rate`, o `reliability` fallback.| 0.20       |
| `cost`         | `1.0 - cost/max_cost` (menor coste = mayor score).      | 0.10         |
| `latency`      | `1.0 - latency/max_latency` (menor latencia = mayor).   | 0.10         |
| `reliability`  | `metadata.reliability` declarado.                       | 0.10         |

Los pesos se normalizan automáticamente a suma 1.0 vía
`RankerWeights.normalized()`. El resultado es una lista de
`RankedCandidate` con `score`, `breakdown` (contribución por factor) y
`rank` (1-indexado).

Empates se resuelven alfabéticamente por `name` (determinístico).

### 3.6 `ToolSelector` (`tool_selector.py`)

API pública de alto nivel. Orquesta el flujo completo:

```
UserIntent + SelectionContext
        │
        ▼
1. Gather candidates (by_capability + by_keyword + by_category)
2. Filter by max_risk_level
3. Filter by runtime constraints (max_cost, max_latency, require_parallel)
4. Filter by compatibility (CompatibilityChecker)
5. Rank (ToolRanker)
6. Apply top_k limit and min_score filter
        │
        ▼
SelectionResult (ranked candidates + rejected + reason)
```

Métodos principales:

- `select(intent, ctx) -> SelectionResult`
- `select_for_plan(plan, ctx) -> list[SubTaskSelection]` — acepta la
  salida del Planner (lista de subtasks con `suggested_tools`) y
  devuelve candidatos por subtask.
- `select_required_capability(capability, ctx) -> RankedCandidate` —
  lanza `CapabilityMissing` si nadie la provee.
- `metrics()` — total de selecciones, candidatos devueltos, fallos.

El selector acepta tanto un `CapabilityIndex` como un `RegistryCache`
(detecta el tipo automáticamente).

## 4. Excepciones

Todas heredan de `ToolIntelligenceError` para permitir captura conjunta:

| Excepción               | Cuándo se lanza                                        |
|-------------------------|--------------------------------------------------------|
| `ToolIntelligenceError` | Base.                                                  |
| `ToolNotCompatible`     | `CompatibilityChecker.check_or_raise` falla.           |
| `CapabilityMissing`     | `select_required_capability` no encuentra proveedor.   |
| `ToolSelectionError`    | `ToolSelector` no puede resolver el índice o falla.    |
| `RankingError`          | `ToolRanker` falla al scorear (bug interno).           |

Cada excepción lleva `message` y `details: dict` serializable.

## 5. Uso típico

```python
from tool_intelligence import (
    CapabilityIndex, RegistryCache, ToolSelector, UserIntent,
    SelectionContext, ToolMetadata, ToolCategory, RiskLevel,
)

# 1. Construir metadatos (típicamente desde el ToolRegistry existente).
metadatas = [ToolMetadata.from_base_tool(t) for t in registry.all()]

# 2. Crear cache (TTL de 5 minutos).
cache = RegistryCache(loader=lambda: metadatas, ttl_seconds=300)

# 3. Selector con top_k=3.
selector = ToolSelector(cache, top_k=3)

# 4. Intent + contexto.
intent = UserIntent(
    message="lee el archivo de configuración",
    capabilities=["filesystem.read"],
    keywords=["read", "file"],
    max_risk_level=RiskLevel.LOW,
)
ctx = SelectionContext(
    platform="linux",
    granted_permissions=["fs:read"],
    available_models=["gpt-4"],
)

# 5. Seleccionar.
result = selector.select(intent, ctx)
if result.best:
    print(f"Best tool: {result.best.metadata.name} (score={result.best.score:.3f})")
    print(f"Breakdown: {result.best.breakdown}")
```

## 6. Integración con el resto del sistema

El paquete es **independiente**: no importa `packages/tools`,
`packages/core`, ni `packages/multiagent`. La integración se hace en el
sentido contrario — el orquestador o el planner importan
`tool_intelligence` para consultar candidatos.

Compatibilidad con `packages/tools`:

- `ToolMetadata.from_base_tool(tool)` acepta cualquier objeto que exponga
  `name`, `description`, `category`, `permission_level` y `metadata`.
- El mapeo `permission_level → RiskLevel` es 1:1
  (`LOW→LOW`, `MEDIUM→MEDIUM`, `HIGH→HIGH`).
- El mapeo `category → ToolCategory` preserva los valores existentes
  (`shell`, `file`, `web`, `app`, `system`, `custom`) y añade nuevos
  (`llm`, `memory`, `network`, `database`) sin romper los anteriores.

## 7. Pruebas

- 274 tests unitarios en `tests/unit/tool_intelligence/` (7 archivos).
- Cobertura del paquete: **100%**.
- Tests organizados por módulo:
  - `test_exceptions.py` — jerarquía y serialización.
  - `test_models.py` — validación, factories, queries, serialización,
    `from_base_tool` con casos edge.
  - `test_capability_index.py` — indexación, búsquedas, mutaciones,
    inspección, dunders.
  - `test_registry_cache.py` — TTL, invalidación, refresh, upsert,
    incremental, errores del loader, stats.
  - `test_compatibility.py` — cada check individual + combinado + caché.
  - `test_tool_ranker.py` — cada factor de scoring + métricas + errores.
  - `test_tool_selector.py` — selección básica, filtros, plan, cache,
    errores, métricas.
  - `test_public_api.py` — exports, smoke test end-to-end.

## 8. Roadmap (Sprint 4.2+)

- **Tool History Provider**: leer `ToolHistoryStats` desde
  `packages/memory` en lugar de pasarlos manualmente en `SelectionContext`.
- **Embeddings para semantic score**: reemplazar el overlap de keywords
  por similitud coseno de embeddings (Sprint 4.3).
- **Tool Profiler**: observar latencia y coste reales durante la
  ejecución y actualizar dinámicamente los `estimated_*` fields.
- **Integration con Planner**: que `Planner._suggest_tools` use
  `ToolSelector.select_for_plan` en lugar del lookup por nombre directo.
- **Tool Composition**: detectar pares de herramientas que se complementan
  (e.g. `file_read` + `file_write`) y proponerlas como unidad.
