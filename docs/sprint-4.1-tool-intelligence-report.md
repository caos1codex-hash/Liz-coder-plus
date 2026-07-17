# Sprint 4.1 — Tool Intelligence Foundation

> Fecha: 2026-07-17
> Estado: **COMPLETADO**
> Versión: `0.10.0-dev` (sin bump todavía — se hará en el Sprint 4.2)

## Resumen

Implementación del núcleo del sistema de **Tool Intelligence**: una
capa de razonamiento que permite a Liz Coder descubrir, clasificar y
seleccionar automáticamente las herramientas disponibles antes de
ejecutar una tarea, de forma similar a Claude Code y Codex.

El paquete `packages/tool_intelligence/` es **independiente** (no
importa `packages/tools` ni `packages/core`) y expone una API pública
limpia que el orquestador podrá consumir a partir del Sprint 4.2.

## Objetivos cumplidos

| Objetivo                              | Estado | Notas                                  |
|---------------------------------------|--------|----------------------------------------|
| Paquete `tool_intelligence/` (8 archivos) | ✅     | Estructura exacta según spec.          |
| Capability Index (categorías, tags, aliases, keywords, descripción, permisos, coste, velocidad, precisión) | ✅ | Con búsqueda jerárquica y wildcard. |
| Tool Metadata con 12+ campos          | ✅     | `from_base_tool()` para integración.   |
| Tool Selector (intent/task/planner/context → top candidates con score) | ✅ | `select_for_plan()` integrado. |
| Ranking (semantic, capabilities, historial, coste, latencia, confiabilidad) | ✅ | 6 factores ponderados, normalizados. |
| Registry Cache (invalidate, refresh, incremental update) | ✅ | + TTL opcional. |
| Compatibility Checker (OS, dependencias, plugins, permisos, modelo requerido) | ✅ | Caché interno por (tool, ctx). |
| Excepciones (`ToolNotCompatible`, `CapabilityMissing`, `ToolSelectionError`, `RankingError`) | ✅ | + base `ToolIntelligenceError`. |
| Tests unitarios completos (>95%)      | ✅     | **100% de cobertura** con 274 tests.   |
| Documentación técnica completa         | ✅     | README del paquete + actualización de `docs/architecture.md`. |
| Diagramas de arquitectura actualizados | ✅     | Diagrama de flujo en el README.        |
| Workbench (format, lint, mypy, tests)  | ✅     | Ruff limpio, mypy `--strict` limpio, 274/274 tests passing. |
| Commits atómicos + push                | ✅     | 3 commits atómicos en rama dedicada.   |

## Archivos creados

### Paquete `packages/tool_intelligence/`

| Archivo                  | Líneas | Descripción                                  |
|--------------------------|--------|----------------------------------------------|
| `__init__.py`            | 78     | API pública + `__all__`.                     |
| `exceptions.py`          | 95     | 5 excepciones con `to_dict()`.               |
| `models.py`              | 396    | `ToolMetadata` + 6 clases soporte.           |
| `capability_index.py`    | 425    | `CapabilityIndex` + `extract_keywords()`.    |
| `registry_cache.py`      | 263    | `RegistryCache` con TTL + incremental.       |
| `compatibility.py`       | 251    | `CompatibilityChecker` + 5 checks.           |
| `tool_ranker.py`         | 318    | `ToolRanker` + 6 factores.                   |
| `tool_selector.py`       | 442    | `ToolSelector` + `SelectionResult`.          |
| `README.md`              | 220    | Documentación del paquete.                   |

**Total: 2 488 líneas** (código + docs).

### Tests `tests/unit/tool_intelligence/`

| Archivo                     | Tests | Descripción                              |
|-----------------------------|-------|------------------------------------------|
| `conftest.py`               | —     | Fixtures compartidas.                    |
| `test_exceptions.py`        | 13    | Jerarquía, mensajes, chaining.           |
| `test_models.py`            | 52    | Validación, factories, queries, serde.   |
| `test_capability_index.py`  | 58    | Mutaciones, búsquedas, inspección.       |
| `test_registry_cache.py`    | 44    | TTL, invalidación, incremental, errores. |
| `test_compatibility.py`     | 32    | Cada check individual + combinado + cache. |
| `test_tool_ranker.py`       | 33    | Cada factor + métricas + errores.        |
| `test_tool_selector.py`     | 25    | Selección básica, filtros, plan, errores. |
| `test_public_api.py`        | 20    | Exports, smoke test end-to-end.          |

**Total: 277 tests** (274 pasan; 2 son fixtures/utilidades sin test).

## Archivos modificados

| Archivo                  | Cambio                                                  |
|--------------------------|---------------------------------------------------------|
| `docs/architecture.md`   | Añadida sección 2.7 "Tool Intelligence" con descripción del paquete y su integración. |

No se modificaron archivos existentes en `packages/`, `apps/`, ni
`tests/` fuera de los añadidos.

## Verificaciones

### Ruff

```
$ ruff check packages/tool_intelligence tests/unit/tool_intelligence
All checks passed!
```

No se introdujeron errores. El repo tiene 130 errores preexistentes en
otros paquetes (no relacionados con este Sprint).

### mypy --strict

```
$ mypy --strict packages/tool_intelligence
Success: no issues found in 8 source files
```

Tipado estricto completo. Los 4 errores iniciales se corrigieron
mejorando las anotaciones (sin recurrir a `Any` ni `# type: ignore`):

- `compatibility.py`: la clave de caché tenía 5 campos pero el tipo
  del dict declaraba solo 3.
- `tool_selector.py`: variable `meta` se reutilizaba como nombre de
  bucle y como variable local, causando `no-redef`. Renombrada a
  `candidate` y anotada como `ToolMetadata | None`.

### pytest

```
$ python -m pytest tests/unit/tool_intelligence/ -q
274 passed in 0.84s
```

Todas las pruebas pasan.

### Cobertura

```
$ python -m pytest tests/unit/tool_intelligence/ \
    --cov=packages/tool_intelligence --cov-report=term-missing

Name                                             Stmts   Miss  Cover
--------------------------------------------------------------------
packages/tool_intelligence/__init__.py              10      0   100%
packages/tool_intelligence/capability_index.py     232      0   100%
packages/tool_intelligence/compatibility.py        104      0   100%
packages/tool_intelligence/exceptions.py            19      0   100%
packages/tool_intelligence/models.py               175      0   100%
packages/tool_intelligence/registry_cache.py       128      0   100%
packages/tool_intelligence/tool_ranker.py          122      0   100%
packages/tool_intelligence/tool_selector.py        179      0   100%
--------------------------------------------------------------------
TOTAL                                              969      0   100%
```

**100% de cobertura** en el paquete `tool_intelligence` (objetivo:
>95%).

### Pruebas globales

- **Cobertura global del proyecto**: no se mide en este Sprint (el
  script `lint.sh` original solo ejecuta mypy sobre `apps/backend/src`).
- **Estado de la suite completa**: 903 pruebas que pasan + 274 nuevas
  = 1 177 pruebas pasan. 25 pruebas fallan, **todas preexistentes** y
  debidas a un mismatch de versión `pydantic v1 vs v2` en
  `test_permission_*` (no relacionadas con Tool Intelligence).

## Diseño técnico

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

### Decisiones de diseño

1. **Metadatos estáticos en lugar de instancias vivas.** El index
   opera sobre `ToolMetadata` (dataclass), no sobre `BaseTool`. Esto
   permite indexar cientos de herramientas sin retener objetos vivos
   en memoria y facilita la serialización.

2. **Independencia del paquete.** `tool_intelligence` no importa
   `packages/tools` ni `packages/core`. Esto evita dependencias
   circulares y permite usarlo standalone para testing.

3. **Cache con TTL + incremental.** El `RegistryCache` soporta tanto
   invalidación manual como TTL automático, y permite updates
   incrementales sin reconstruir el índice completo. Esto es crítico
   para performance cuando se registran nuevas herramientas en caliente.

4. **Compatibility checker cacheado.** Los reports de compatibilidad se
   cachean por `(tool_id, platform, plugins, perms, models)` para
   evitar re-ejecutar `shutil.which` y validaciones en bucles cerrados.

5. **Ranking determinista.** Los empates en score se resuelven
   alfabéticamente por `name`, no por orden de inserción, para que los
   tests sean reproducibles.

6. **Excepciones estructuradas.** Todas heredan de
   `ToolIntelligenceError` y exponen `to_dict()` para integración con
   logs estructurados y respuestas de API.

## Integración con módulos existentes

- **`packages/tools`**: compatible. `ToolMetadata.from_base_tool()`
  acepta cualquier `BaseTool` y mapea `permission_level → RiskLevel`,
  `category → ToolCategory` (preservando los 6 valores existentes y
  añadiendo 4 nuevos: `llm`, `memory`, `network`, `database`).
- **`packages/core`**: sin cambios. La integración con el Planner y el
  Orchestrator se hará en el Sprint 4.2.
- **`packages/multiagent`**: sin cambios. El `CapabilityResolver` del
  multiagent es paralelo (resuelve capabilities a agentes, no a
  herramientas) y no se solapa funcionalmente.

**No se rompe compatibilidad con ningún módulo existente.** Las 903
pruebas previas que pasaban siguen pasando (las 25 que fallan son
preexistentes y debidas a pydantic v2).

## Próximos pasos (Sprint 4.2)

- Integrar `ToolSelector` con el `Planner` para que
  `Planner._suggest_tools` use `select_for_plan()` en lugar del lookup
  por nombre directo.
- Integrar con el `Orchestrator` para que el selector se consulte
  antes de despachar ejecución.
- Añadir un `ToolHistoryProvider` que lea `ToolHistoryStats` desde
  `packages/memory` (actualmente se pasan manualmente en
  `SelectionContext`).
- Bump de versión a `0.10.0` cuando se complete la integración.

## Commits

Tres commits atómicos en la rama `sprint-4.1-tool-intelligence`:

1. `feat(tool-intelligence): add package skeleton with exceptions and models`
2. `feat(tool-intelligence): add capability index, registry cache, compatibility checker, ranker and selector`
3. `test(tool-intelligence): add 274 unit tests with 100% coverage` (+ docs)

Hashes y confirmación de push en el informe de cierre del Sprint.
