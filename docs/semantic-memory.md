# Memoria Semántica — Sprint 2.7

## Descripción General

El sistema de memoria semántica permite recuperar información por significado, no solo por coincidencia textual. Transforma el sistema de memoria de Sprint 2.6 en una memoria capaz de comprender el contexto semántico de las consultas.

## Arquitectura

La capa semántica está completamente desacoplada del resto del sistema:

```
MemoryEngine (Sprint 2.6)
    ↓
SemanticIndex (Sprint 2.7)
    ↓                    ↓
EmbeddingProvider    VectorStore
    ↓                    ↓
EmbeddingCache     InMemoryVectorStore
```

## Componentes

### EmbeddingProvider
Interfaz abstracta para generar embeddings de texto. Operaciones:
- `embed_text(text)` → EmbeddingResult
- `embed_batch(texts)` → List[EmbeddingResult]
- `dimensions()` → int
- `provider_name()` → str
- `health()` → Dict

### VectorStore
Interfaz abstracta para almacenar y buscar vectores. Operaciones:
- `add(id, vector, metadata)`
- `update(id, vector, metadata)`
- `remove(id)`
- `search(query_vector, top_k, min_score, filters)`
- `get(id)` → (vector, metadata)
- `clear()` → count
- `stats()`

### SemanticIndex
Puente entre embeddings y MemoryEngine. Responsabilidades:
- Genera embeddings para registros de memoria
- Almacena vectores en el VectorStore
- Mantiene sincronización con MemoryEngine
- Proporciona búsqueda por similitud

### SemanticRetrievalEngine
Motor de búsqueda unificado con tres modos:
- **SEMANTIC**: Búsqueda por significado vía embeddings
- **TEXT**: Búsqueda por palabras clave vía MemorySearchIndex
- **HYBRID**: Combina ambos, fusiona y re-rankea

### RankingEngine
Combina múltiples señales para rankear resultados:
- Similitud semántica (peso por defecto: 0.5)
- Prioridad del registro (0.2)
- Recencia (0.15)
- Frecuencia de uso (0.1)
- Tipo de memoria (0.05)

### EmbeddingCache
Cache LRU thread-safe para embeddings. Características:
- Tamaño máximo configurable (default: 10,000)
- Hit/miss statistics
- Evicción automática LRU
- Batch get/put

## Integración con ContextEngine

ContextEngine ahora acepta `use_semantic=True` en ContextRequest para incluir resultados semánticos en el contexto. Si el SemanticRetrievalEngine falla, se hace fallback automático a búsqueda textual.

## Proveedores Disponibles

| Proveedor | Estado | Notas |
|-----------|--------|-------|
| DummyEmbeddingProvider | ✅ Implementado | Para testing y desarrollo |
| OpenAI | 🔜 Futuro | Requiere API key |
| Ollama | 🔜 Futuro | Local, sin dependencia externa |
| Sentence Transformers | 🔜 Futuro | Open source, local |
| NVIDIA NIM | 🔜 Futuro | GPU-acelerado |
| HuggingFace | 🔜 Futuro | Open source |
| Gemini | 🔜 Futuro | Google AI |

## Vector Stores Disponibles

| Store | Estado | Notas |
|-------|--------|-------|
| InMemoryVectorStore | ✅ Implementado | Para testing, <10K vectores |
| FAISS | 🔜 Futuro | Alto rendimiento, local |
| ChromaDB | 🔜 Futuro | Embeddings integrados |
| Qdrant | 🔜 Futuro | Distribuido, filtrado avanzado |
| Milvus | 🔜 Futuro | Escala empresarial |
| Weaviate | 🔜 Futuro | Hybrid search nativo |
| pgvector | 🔜 Futuro | PostgreSQL integrado |