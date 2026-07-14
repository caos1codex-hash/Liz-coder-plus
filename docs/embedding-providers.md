# Embedding Providers — Sprint 2.7

## Interfaz

```python
class EmbeddingProvider(ABC):
    async def embed_text(text: str) -> EmbeddingResult
    async def embed_batch(texts: List[str]) -> List[EmbeddingResult]
    def dimensions() -> int
    def provider_name() -> str
    async def health() -> Dict
    def compute_text_hash(text: str) -> str
```

## EmbeddingResult

```python
@dataclass
class EmbeddingResult:
    vector: List[float]
    text_hash: str       # SHA-256 del texto
    provider: str        # Nombre del proveedor
    dimensions: int      # Dimensión del vector
    latency_ms: float    # Tiempo de generación
    metadata: Dict       # Metadata del proveedor

    def normalized() -> List[float]  # Vector unitario
    def magnitude() -> float         # Norma euclidiana
```

## DummyEmbeddingProvider

Proveedor determinista para testing. Genera vectores pseudo-aleatorios a partir del hash SHA-256 del texto de entrada.

### Características
- Determinista: mismo texto → mismo vector
- Vectores normalizados a unit length
- Sin dependencias externas
- Latencia simulable
- Seed configurable

### Uso

```python
provider = DummyEmbeddingProvider(dims=128, seed=42)
result = await provider.embed_text("hello world")
# result.vector → [0.05, -0.12, ...]  (128 dimensiones)
# result.magnitude → 1.0
```

## Agregar un Nuevo Proveedor

1. Crear `mi_proveedor.py` en `multiagent/semantic/`
2. Heredar de `EmbeddingProvider`
3. Implementar los 5 métodos abstractos
4. Opcionalmente usar `compute_text_hash()` del base para caching

```python
class MiProveedor(EmbeddingProvider):
    async def embed_text(self, text: str) -> EmbeddingResult:
        # Llamar API real
        vector = await self._api.embed(text)
        return EmbeddingResult(
            vector=vector,
            text_hash=self.compute_text_hash(text),
            provider=self.provider_name(),
            dimensions=len(vector),
        )

    async def embed_batch(self, texts): ...
    def dimensions(self): return 768
    def provider_name(self): return "mi_proveedor"
    async def health(self): return {"status": "healthy"}
```

## Proveedores Planeados

| Proveedor | Dimensiones | Latencia Típica | Requiere |
|-----------|------------|-----------------|----------|
| OpenAI (text-embedding-3-small) | 1536 | ~50ms | API key, internet |
| Ollama (nomic-embed-text) | 768 | ~20ms | Ollama local |
| Sentence Transformers | 384-768 | ~5ms | modelo local |
| NVIDIA NIM | 1024+ | ~10ms | GPU, NVIDIA API |
| Gemini | 768 | ~30ms | Google API key |