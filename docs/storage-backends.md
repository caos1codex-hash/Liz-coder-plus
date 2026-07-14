# Backends de Almacenamiento — Guía de Referencia

> **Módulo:** `packages/multiagent/src/multiagent/memctx/storage.py`
> **Versión:** 0.1.0
> **Última actualización:** Sprint 2.6

---

## 1. Interfaz MemoryStorage (ABC)

`MemoryStorage` es la clase base abstracta que todo backend de almacenamiento debe implementar. Define 9 métodos que cubren el ciclo de vida completo de los registros:

```python
class MemoryStorage(ABC):
    @abstractmethod
    async def save(self, record: MemoryRecord) -> MemoryRecord:
        """Persistir un nuevo registro. Debe lanzar ValueError si el ID ya existe."""
        ...

    @abstractmethod
    async def update(self, record: MemoryRecord) -> MemoryRecord:
        """Actualizar un registro existente. Debe lanzar KeyError si no existe
        y ValueError si hay conflicto de versión."""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Eliminar por ID. Retorna True si existía, False si no."""
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Recuperar un registro por ID, o None si no existe."""
        ...

    @abstractmethod
    async def exists(self, memory_id: str) -> bool:
        """Verificar si un registro existe."""
        ...

    @abstractmethod
    async def search(
        self,
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """Búsqueda con filtros AND opcionales. Ordenar por (priority DESC, updated_at DESC)."""
        ...

    @abstractmethod
    async def list(
        self,
        owner: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """Listar registros con filtros opcionales."""
        ...

    @abstractmethod
    async def clear(self, owner: Optional[str] = None) -> int:
        """Eliminar todos los registros (o los de un owner). Retorna cantidad eliminada."""
        ...

    @abstractmethod
    async def count(self, owner: Optional[str] = None) -> int:
        """Contar registros, opcionalmente filtrados por owner."""
        ...
```

**Contratos que toda implementación debe respetar:**

1. **Seguridad de hilos:** Los métodos pueden ser invocados concurrentemente desde múltiples coroutines.
2. **Control de versiones optimista:** `update()` debe verificar que `record.version` coincida con la versión almacenada, incrementándola automáticamente.
3. **Detección de duplicados:** `save()` debe rechazar IDs duplicados con `ValueError`.
4. **Ordenamiento:** `search()` y `list()` deben retornar resultados ordenados por `(priority DESC, updated_at DESC)`.
5. **Filtrado AND:** Todos los parámetros de filtro en `search()` son combinados con lógica AND.

---

## 2. InMemoryStorage

### 2.1 Descripción

Backend basado en un diccionario Python (`Dict[str, MemoryRecord]`). Todos los datos residen en memoria y se pierden al finalizar el proceso.

### 2.2 Cuándo usarlo

- **Pruebas unitarias y de integración:** Rápido, sin dependencias externas, sin efectos secundarios en disco.
- **Sesiones efímeras:** Chatbots de una sola sesión, prototipos, demos.
- **Caché en memoria:** Como capa de caché frente a un backend persistente.
- **Desarrollo local:** Iteración rápida sin configurar una base de datos.

### 2.3 Seguridad de hilos

Todas las operaciones están protegidas por `threading.Lock()`:

```python
class InMemoryStorage(MemoryStorage):
    def __init__(self) -> None:
        self._store: Dict[str, MemoryRecord] = {}
        self._lock = threading.Lock()

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            if record.id in self._store:
                raise ValueError(f"duplicate memory id: {record.id}")
            self._store[record.id] = record
        return record
```

### 2.4 Limitaciones

| Limitación | Impacto |
|-----------|---------|
| **Volátil** | Los datos se pierden al reiniciar el proceso |
| **Acotado por RAM** | No es adecuado para conjuntos de datos grandes (>100k registros) |
| **Sin consultas avanzadas** | La búsqueda de texto es substring simple, no soporta regex avanzado ni búsqueda semántica |
| **Búsqueda lineal** | `search()` itera sobre todos los registros; O(n) por consulta |
| **Sin persistencia** | No hay WAL, journaling ni recuperación tras crash |

### 2.5 Instanciación

```python
from packages.multiagent.src.multiagent.memctx import InMemoryStorage, MemoryEngine

storage = InMemoryStorage()
engine = MemoryEngine(storage=storage)
```

---

## 3. FileStorage

### 3.1 Descripción

Backend persistente que almacena cada registro como un archivo JSON individual en el sistema de archivos.

### 3.2 Estructura de directorios

```
.memory/                          ← base_path (configurable)
├── conversation/
│   ├── a1b2c3d4-....json
│   └── e5f6g7h8-....json
├── project/
│   └── ...
├── workflow/
│   └── ...
├── knowledge/
│   └── ...
└── context/
    └── ...
```

Cada subdirectorio corresponde a un valor de `MemoryType`. El nombre del archivo es `{memory_id}.json`.

### 3.3 Formato de archivo

Cada archivo contiene el JSON producido por `MemoryRecord.to_dict()`:

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "memory_type": "knowledge",
  "content": {"text": "Python 3.11+ soporta match/case"},
  "owner": "system",
  "tags": ["python", "syntax"],
  "metadata": {"source": "docs"},
  "version": 1,
  "created_at": "2026-07-15T10:30:00+00:00",
  "updated_at": "2026-07-15T10:30:00+00:00",
  "ttl_seconds": null,
  "priority": 5
}
```

### 3.4 Reconstrucción del índice

Al instanciar `FileStorage`, se escanea el directorio base para reconstruir un índice en memoria que mapea `memory_id → ruta_relativa`:

```python
def _load_index(self) -> None:
    self._index.clear()
    if not self._base_path.exists():
        return
    for mt_dir in self._base_path.iterdir():
        if mt_dir.is_dir():
            for f in mt_dir.glob("*.json"):
                memory_id = f.stem
                rel = str(f.relative_to(self._base_path))
                self._index[memory_id] = rel
```

Este índice evita leer todos los archivos en cada operación y proporciona una verificación rápida de existencia.

### 3.5 Concurrencia

Al igual que `InMemoryStorage`, usa `threading.Lock()` para proteger todas las operaciones contra escritura. Esto es suficiente para uso local, pero **no es adecuado para procesos múltiples escribiendo al mismo directorio** (se necesitaría un lock a nivel de archivo o una base de datos).

### 3.6 Tolerancia a corrupción

Durante la búsqueda, los archivos que no se pueden leer (JSON inválido, claves faltantes) se registran como advertencias sin interrumpir la operación:

```python
try:
    rec = self._read_record(fp)
except (json.JSONDecodeError, KeyError, ValueError):
    logger.warning("corrupt record file: %s", fp)
    continue
```

### 3.7 Instanciación

```python
from packages.multiagent.src.multiagent.memctx import FileStorage, MemoryEngine

storage = FileStorage(base_path="/ruta/a/.memory")
engine = MemoryEngine(storage=storage)
```

---

## 4. Implementar un backend personalizado

### 4.1 Esqueleto mínimo

```python
from packages.multiagent.src.multiagent.memctx.storage import MemoryStorage
from packages.multiagent.src.multiagent.memctx.models import MemoryRecord, MemoryType

class MiStoragePersonalizado(MemoryStorage):
    """Ejemplo de backend personalizado."""

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        # 1. Verificar que no existe (duplicado)
        # 2. Persistir el registro
        # 3. Retornar el registro guardado
        ...

    async def update(self, record: MemoryRecord) -> MemoryRecord:
        # 1. Recuperar existente, verificar versión
        # 2. Actualizar con version+1
        # 3. Retornar el registro actualizado
        ...

    async def delete(self, memory_id: str) -> bool:
        ...

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        ...

    async def exists(self, memory_id: str) -> bool:
        ...

    async def search(self, query=None, tags=None, owner=None,
                     memory_type=None, metadata_filter=None,
                     limit=50, offset=0) -> list:
        ...

    async def list(self, owner=None, memory_type=None, limit=100, offset=0) -> list:
        ...

    async def clear(self, owner=None) -> int:
        ...

    async def count(self, owner=None) -> int:
        ...
```

### 4.2 Ejemplo: Backend SQLite

```python
import aiosqlite
from pathlib import Path

class SQLiteStorage(MemoryStorage):
    def __init__(self, db_path: str = "memory.db") -> None:
        self._db_path = db_path

    async def _get_conn(self):
        return await aiosqlite.connect(self._db_path)

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        import json
        async with self._get_conn() as conn:
            await conn.execute(
                "INSERT OR FAIL INTO memories (id, data) VALUES (?, ?)",
                (record.id, json.dumps(record.to_dict()))
            )
            await conn.commit()
        return record

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        import json
        async with self._get_conn() as conn:
            async with conn.execute(
                "SELECT data FROM memories WHERE id = ?", (memory_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return MemoryRecord.from_dict(json.loads(row[0]))

    # ... implementar el resto de métodos ...
```

### 4.3 Ejemplo: Backend Redis

```python
import redis.asyncio as redis

class RedisStorage(MemoryStorage):
    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self._client = redis.from_url(url)
        self._prefix = "mem:"

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        import json
        key = f"{self._prefix}{record.id}"
        data = json.dumps(record.to_dict())
        if await self._client.exists(key):
            raise ValueError(f"duplicate memory id: {record.id}")
        await self._client.set(key, data)
        return record

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        import json
        key = f"{self._prefix}{memory_id}"
        data = await self._client.get(key)
        if data is None:
            return None
        return MemoryRecord.from_dict(json.loads(data))

    # ... implementar el resto de métodos ...
```

### 4.4 Ejemplo: Backend PostgreSQL

```python
import asyncpg

class PostgreSQLStorage(MemoryStorage):
    def __init__(self, dsn: str) -> None:
        self._pool = await asyncpg.create_pool(dsn)

    async def save(self, record: MemoryRecord) -> MemoryRecord:
        import json
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO memory_records (id, memory_type, owner, content, tags, metadata, version, ttl_seconds, priority)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                record.id, record.memory_type.value, record.owner,
                json.dumps(record.content), json.dumps(record.tags),
                json.dumps(record.metadata), record.version,
                record.ttl_seconds, record.priority,
            )
        return record

    # ... implementar el resto de métodos ...
```

---

## 5. Guía de selección de backend

| Criterio | InMemoryStorage | FileStorage | SQLite | PostgreSQL | Redis |
|----------|----------------|-------------|--------|------------|-------|
| **Persistencia** | No | Sí (archivos) | Sí | Sí | Sí (con RDB/AOF) |
| **Velocidad** | Muy alta | Media | Alta | Media-Alta | Muy alta |
| **Concurrencia multi-proceso** | No | No | Sí | Sí | Sí |
| **Consultas avanzadas** | No | No | SQL completo | SQL completo | Patrones básicos |
| **Escala** | <100k registros | <10k archivos | ~Millones | Billones | Millones |
| **Configuración** | Ninguna | Solo ruta | Archivo DB | DSN + schema | URL |
| **Uso recomendado** | Tests, prototipos | Desarrollo local | Despliegue single-server | Producción multi-instancia | Caché, baja latencia |

---

## 6. Migración entre backends

La migración se realiza leyendo desde el backend origen y escribiendo al destino a través de la API estándar de `MemoryEngine`:

```python
async def migrar(origen: MemoryStorage, destino: MemoryStorage) -> int:
    """Migrar todos los registros de un backend a otro."""
    engine_origen = MemoryEngine(storage=origen)
    engine_destino = MemoryEngine(storage=destino)
    await engine_origen.start()
    await engine_destino.start()

    registros = await engine_origen.list_records(limit=100_000)
    migrados = await engine_destino.store_many(registros)

    await engine_origen.stop()
    await engine_destino.stop()
    return len(migrados)

# Ejemplo: de InMemory a FileStorage
count = await migrar(InMemoryStorage(), FileStorage("/nueva/ruta/.memory"))
print(f"Migrados {count} registros")
```

**Consideraciones:**
- Los IDs se preservan durante la migración (no se regeneran).
- Los timestamps de creación y actualización se mantienen intactos.
- El `MemorySearchIndex` del destino se reconstruye automáticamente al llamar a `store()`.
- Para conjuntos de datos muy grandes, procesar en lotes con `list_records(limit=1000, offset=N)`.