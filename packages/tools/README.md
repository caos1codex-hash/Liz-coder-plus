# Tools Package

> Herramientas externas para Liz Coder Plus.

## Estado

**Sprint 4.2 — Tool Intelligence** — Motor de selección automática de
herramientas con filtrado multi-criterio y ranking ponderado.

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
├── __init__.py       → API pública del paquete (v0.3.0)
├── base.py           → Clase base BaseTool
├── registry.py       → Registro de herramientas disponibles
├── exceptions.py     → Jerarquía de excepciones del motor de selección
├── filters.py        → 8 filtros independientes y reutilizables
├── ranking.py        → Scoring multi-factor con pesos configurables
└── selection.py      → ToolSelectionEngine (punto de entrada principal)
```

## Motor de Selección (Sprint 4.2)

### Pipeline

```
ToolRegistry.all()
       │
       ▼
  ┌─────────────┐
  │  Filtros     │  ← 8 filtros en cadena:
  │  (chain)     │    Availability → Capability → ExecutionMode →
  │              │    Permission → Cost → Latency → Safety →
  │              │    UserPreference
  └──────┬──────┘
         │ candidatos supervivientes
         ▼
  ┌─────────────┐
  │  Ranking     │  ← 8 factores ponderados:
  │  (scoring)   │    Capability Match, Priority, Latency, Cost,
  │              │    Reliability, Historical Performance, Safety,
  │              │    Manual Boost
  └──────┬──────┘
         │ RankingResult[] ordenados
         ▼
  select_tool() → BaseTool
  select_tools(top_n=N) → list[BaseTool]
  rank_tools() → list[RankingResult]
```

### Uso básico

```python
from src import ToolSelectionEngine, ToolRegistry
from src.filters import ExecutionMode

engine = ToolSelectionEngine(registry)

# Seleccionar la mejor herramienta para una tarea de archivos
tool = engine.select_tool(
    task_name="read_config",
    capability_requirements=frozenset({"file"}),
    execution_mode=ExecutionMode.AUTOMATIC,
)

# Obtener top-3 herramientas
top_3 = engine.select_tools(top_n=3)
```

### Filtros disponibles

| Filtro | Elimina herramientas que... |
|--------|------------------------------|
| `AvailabilityFilter` | No están en estado AVAILABLE |
| `CapabilityFilter` | No cubren las capacidades requeridas |
| `ExecutionModeFilter` | Son incompatibles con el modo de ejecución |
| `PermissionFilter` | Exceden el nivel de permiso máximo |
| `CostFilter` | Superan el presupuesto de costo |
| `LatencyFilter` | Superan el umbral de latencia |
| `SafetyFilter` | Están por debajo del score de seguridad mínimo |
| `UserPreferenceFilter` | Están en la lista de exclusión del usuario |

### Factores de ranking

| Factor | Peso default | Descripción |
|--------|-------------|-------------|
| `capability_weight` | 0.30 | Solapamiento Jaccard de capacidades |
| `priority_weight` | 0.15 | Prioridad explícita en metadata |
| `reliability_weight` | 0.15 | Tasa de éxito (executions / total) |
| `latency_weight` | 0.10 | Inverso de latencia promedio |
| `cost_weight` | 0.10 | Inverso del costo declarado |
| `history_weight` | 0.10 | Confianza por volumen de ejecuciones |
| `safety_weight` | 0.05 | Score de seguridad |
| `manual_boost_weight` | 0.05 | Boost explícito o preferencia de usuario |

### Excepciones

| Excepción | Cuándo se lanza |
|-----------|----------------|
| `NoToolFoundError` | Ninguna herramienta pasa los filtros |
| `AmbiguousSelectionError` | Múltiples herramientas empatan en el top |
| `RankingError` | Error al computar el score |
| `FilterError` | Error inesperado en un filtro |