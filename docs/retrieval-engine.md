# Retrieval Engine — Sprint 2.7

## Descripción

El SemanticRetrievalEngine es el punto de entrada unificado para todas las búsquedas de memoria. Soporta tres modos de búsqueda y integra ranking multi-signal.

## Modos de Búsqueda

### SEMANTIC
Búsqueda por significado usando embeddings. Requiere un SemanticIndex configurado.

### TEXT
Búsqueda por palabras clave usando el MemorySearchIndex existente (Sprint 2.6).

### HYBRID (recomendado)
Combina resultados de búsqueda semántica y textual, fusiona duplicados y re-rankea.

## Flujo de Búsqueda Híbrida

```
Query
  ├─→ SemanticIndex.search() → resultados semánticos
  ├─→ MemorySearchIndex.search() → resultados textuales
  └─→ Merge por ID (sin duplicados)
        └─→ RankingEngine.rank() → resultados finales ordenados
```

## RetrievalRequest

```python
@dataclass
class RetrievalRequest:
    query: str                    # Texto de búsqueda
    mode: RetrievalMode           # semantic | text | hybrid
    top_k: int = 10               # Máximo resultados
    min_score: float = 0.0        # Score mínimo
    owner: Optional[str]          # Filtro por owner
    memory_types: Optional[List]  # Filtro por tipo
    tags: Optional[List[str]]     # Filtro por tags
    metadata_filter: Optional[Dict]
    include_metadata: bool = True
    ranking_config: Optional[RankingConfig]
```

## Ranking Multi-Signal

Cada resultado recibe un score final combinado:

```
final_score = Σ(signal_value × weight) / Σ(weights)
```

Señales disponibles:
- **semantic_similarity**: Similitud coseno (0.0-1.0)
- **priority**: Prioridad del registro normalizada
- **recency**: Decaimiento exponencial desde updated_at
- **frequency**: Veces que el registro apareció en resultados
- **memory_type**: Preferencia configurada por tipo

## Integración con ContextEngine

```python
retrieval = SemanticRetrievalEngine(memory_engine, ...)
ctx_engine = ContextEngine(memory_engine, semantic_retrieval=retrieval)

request = ContextRequest(
    query="Python programming",
    use_semantic=True,
    semantic_top_k=20,
)
result = await ctx_engine.build_context(request)
```

Si la búsqueda semántica falla, se hace fallback automático a búsqueda textual.