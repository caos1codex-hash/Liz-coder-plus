# Vector Store — Sprint 2.7

## API Abstracta

```python
class VectorStore(ABC):
    async def add(id: str, vector: List[float], metadata: Dict) -> None
    async def add_batch(ids, vectors, metadatas) -> int
    async def update(id: str, vector: List[float], metadata: Dict) -> bool
    async def remove(id: str) -> bool
    async def search(query_vector, top_k=10, min_score=0.0, filters=None) -> List[VectorSearchResult]
    async def get(id: str) -> Optional[Tuple[List[float], Dict]]
    async def exists(id: str) -> bool
    async def clear() -> int
    async def size() -> int
    def stats() -> Dict
```

## InMemoryVectorStore

Implementación basada en diccionarios con búsqueda por fuerza bruta. Thread-safe via `threading.Lock`.

### Características
- Búsqueda por similitud coseno
- Filtros por metadata (key-value, todos deben coincidir)
- Validación de dimensiones (opcional)
- Estadísticas de uso

### Limitaciones
- Búsqueda O(n) — no escalable más allá de ~10K vectores
- Los vectores se mantienen en memoria
- Sin persistencia

### Uso

```python
store = InMemoryVectorStore(expected_dims=128)
await store.add("doc-1", [0.1, 0.2, ...], {"type": "knowledge"})
results = await store.search(query_vector, top_k=5, min_score=0.7)
```

## Funciones de Similitud

- `cosine_similarity(a, b)` → float [-1.0, 1.0]
- `euclidean_distance(a, b)` → float [0.0, ∞)
- `dot_product(a, b)` → float (-∞, ∞)

## Migración a Otros Stores

Para usar FAISS, ChromaDB, etc.:

1. Crear una clase que herede de `VectorStore`
2. Implementar todos los métodos abstractos
3. Pasar la instancia a `SemanticIndex(vector_store=tu_store)`

No se requiere modificar ningún otro componente.