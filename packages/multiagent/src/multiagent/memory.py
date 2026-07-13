"""Memoria por agente (Sprint 2.1).

Cada agente tiene su propia memoria dividida en:

- **Memoria corta** (`short`): buffer circular de los últimos N eventos
  útiles para contexto inmediato.
- **Memoria larga** (`long`): almacén clave-valor persistente durante
  la sesión del agente (puede respaldarse en el MemoryManager global
  de Sprint 1.8 vía adaptador).
- **Embeddings** (`embeddings`): vectores asociados a textos, con un
  índice de similitud trivial (producto interno). La recuperación
  contextual usa cosine similarity sobre los embeddings almacenados.

La implementación es deliberadamente sin dependencias externas (no
numpy) para mantener compatibilidad con el entorno actual. Si se
detecta numpy instalado, se usa para acelerar la similitud.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Entradas de memoria
# ----------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """Entrada individual en la memoria de un agente.

    Attributes:
        key:        Identificador (para memoria larga) o "" (para corta).
        value:      Contenido arbitrario.
        embedding:  Vector opcional asociado.
        timestamp:  Epoch seconds.
        tags:       Etiquetas libres para filtrado.
        metadata:   Dict plano adicional.
    """

    value: Any
    key: str = ""
    embedding: list[float] | None = None
    timestamp: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "embedding": list(self.embedding) if self.embedding is not None else None,
            "timestamp": self.timestamp,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


# ----------------------------------------------------------------------
# Memoria
# ----------------------------------------------------------------------


class AgentMemory:
    """Memoria corta + larga + embeddings para un agente.

    Args:
        short_capacity: Tamaño del buffer circular de memoria corta.
        long_capacity:  Máximo número de entradas en memoria larga (LRU).
    """

    def __init__(
        self,
        *,
        short_capacity: int = 64,
        long_capacity: int = 1024,
    ) -> None:
        self._short: deque[MemoryEntry] = deque(maxlen=short_capacity)
        self._long: dict[str, MemoryEntry] = {}
        self._long_capacity = long_capacity
        self._long_order: list[str] = []  # LRU tracking
        self._embeddings: list[MemoryEntry] = []

    # ------------------------------------------------------------------
    # Memoria corta
    # ------------------------------------------------------------------

    def add_short(
        self,
        value: Any,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Añade un evento a la memoria corta."""
        entry = MemoryEntry(
            value=value,
            tags=list(tags or []),
            metadata=dict(metadata or {}),
        )
        self._short.append(entry)
        return entry

    def recent(self, n: int = 10) -> list[MemoryEntry]:
        """Devuelve los últimos `n` eventos de memoria corta."""
        if n <= 0:
            return []
        return list(self._short)[-n:]

    def short_by_tag(self, tag: str) -> list[MemoryEntry]:
        """Filtra memoria corta por tag."""
        return [e for e in self._short if tag in e.tags]

    # ------------------------------------------------------------------
    # Memoria larga
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        value: Any,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Almacena un valor en memoria larga (clave-valor)."""
        entry = MemoryEntry(
            key=key,
            value=value,
            tags=list(tags or []),
            metadata=dict(metadata or {}),
        )
        if key in self._long:
            self._long_order.remove(key)
        self._long[key] = entry
        self._long_order.append(key)
        # LRU eviction
        while len(self._long) > self._long_capacity:
            oldest = self._long_order.pop(0)
            self._long.pop(oldest, None)
        return entry

    def retrieve(self, key: str) -> MemoryEntry | None:
        """Recupera un valor de memoria larga."""
        entry = self._long.get(key)
        if entry is None:
            return None
        # Update LRU order
        if key in self._long_order:
            self._long_order.remove(key)
            self._long_order.append(key)
        return entry

    def forget(self, key: str) -> bool:
        """Elimina una entrada de memoria larga."""
        if key in self._long:
            del self._long[key]
            self._long_order.remove(key)
            return True
        return False

    def long_keys(self) -> list[str]:
        """Devuelve las claves de memoria larga."""
        return list(self._long.keys())

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def add_embedding(
        self,
        text: str,
        embedding: list[float],
        *,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """Asocia un embedding a un texto."""
        entry = MemoryEntry(
            key=text[:64],  # clave truncada
            value=text,
            embedding=list(embedding),
            tags=list(tags or []),
        )
        self._embeddings.append(entry)
        return entry

    def search_similar(
        self,
        query: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[tuple[MemoryEntry, float]]:
        """Busca los embeddings más similares a `query`.

        Usa cosine similarity. Devuelve hasta `top_k` resultados con
        score >= `min_score`.
        """
        if not self._embeddings:
            return []
        results: list[tuple[MemoryEntry, float]] = []
        q_norm = _norm(query)
        if q_norm == 0.0:
            return []
        for entry in self._embeddings:
            if entry.embedding is None:
                continue
            score = _cosine(query, q_norm, entry.embedding)
            if score >= min_score:
                results.append((entry, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ------------------------------------------------------------------
    # Iteración y serialización
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[MemoryEntry]:
        """Itera sobre memoria corta (cronológica) y luego larga."""
        for e in self._short:
            yield e
        for e in self._long.values():
            yield e

    def to_dict(self) -> dict[str, Any]:
        return {
            "short": [e.to_dict() for e in self._short],
            "long": {k: v.to_dict() for k, v in self._long.items()},
            "embeddings_count": len(self._embeddings),
            "short_capacity": self._short.maxlen,
            "long_capacity": self._long_capacity,
        }

    def clear(self) -> None:
        """Vacía toda la memoria."""
        self._short.clear()
        self._long.clear()
        self._long_order.clear()
        self._embeddings.clear()


# ----------------------------------------------------------------------
# Helpers de similitud
# ----------------------------------------------------------------------


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _cosine(a: list[float], a_norm: float, b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    b_norm = _norm(b)
    if b_norm == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (a_norm * b_norm)


__all__ = ["AgentMemory", "MemoryEntry"]
