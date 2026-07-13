"""MemoryAgent — memoria corta, larga, embeddings, recuperación (Sprint 2.1).

Responsable de:

- memoria corta
- memoria larga
- embeddings
- recuperación contextual

Es el agente "bibliotecario" del sistema: otros agentes le piden
almacenar o recuperar información, y él expone una API uniforme sobre
el `AgentMemory` interno.

Operaciones soportadas (`payload['op']`):

- `store`   — `payload['key']`, `payload['value']`, `payload['tags']`
- `retrieve`— `payload['key']`
- `forget`  — `payload['key']`
- `add_embedding` — `payload['text']`, `payload['embedding']`
- `search`  — `payload['embedding']`, `payload['top_k']`
- `recent`  — `payload['n']`
- `clear`   — sin args
"""

from __future__ import annotations

from typing import Any

from ..base import BaseAgent
from ..enums import Permission


class MemoryAgent(BaseAgent):
    """Agente de memoria."""

    default_permissions = frozenset({
        Permission.MEMORY_READ, Permission.MEMORY_WRITE,
    })
    default_objectives = ["memory", "store", "retrieve", "embed", "search"]
    default_tools = ["memory_store", "vector_index"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="memory", description="Manages short/long-term memory and embeddings", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        op = (payload.get("op") or "recent").lower()

        if op == "store":
            self.require_permission(Permission.MEMORY_WRITE)
            key = payload.get("key") or f"k:{task.get('id', 'unknown')}"
            value = payload.get("value")
            tags = payload.get("tags", [])
            metadata = payload.get("metadata", {})
            entry = self._memory.store(
                key=key, value=value, tags=tags, metadata=metadata,
            )
            return {
                "op": "store",
                "key": key,
                "entry": entry.to_dict(),
                "tools_used": ["memory_store"],
            }

        if op == "retrieve":
            self.require_permission(Permission.MEMORY_READ)
            key = payload.get("key", "")
            entry = self._memory.retrieve(key)
            return {
                "op": "retrieve",
                "key": key,
                "found": entry is not None,
                "entry": entry.to_dict() if entry else None,
                "tools_used": ["memory_store"],
            }

        if op == "forget":
            self.require_permission(Permission.MEMORY_WRITE)
            key = payload.get("key", "")
            removed = self._memory.forget(key)
            return {
                "op": "forget",
                "key": key,
                "removed": removed,
                "tools_used": ["memory_store"],
            }

        if op == "add_embedding":
            self.require_permission(Permission.MEMORY_WRITE)
            text = payload.get("text", "")
            embedding = payload.get("embedding", [])
            if not isinstance(embedding, list):
                return {"op": "add_embedding", "error": "embedding must be a list"}
            entry = self._memory.add_embedding(text, embedding)
            return {
                "op": "add_embedding",
                "entry": entry.to_dict(),
                "tools_used": ["vector_index"],
            }

        if op == "search":
            self.require_permission(Permission.MEMORY_READ)
            query = payload.get("embedding", [])
            top_k = int(payload.get("top_k", 5))
            min_score = float(payload.get("min_score", 0.0))
            results = self._memory.search_similar(
                query, top_k=top_k, min_score=min_score,
            )
            return {
                "op": "search",
                "results": [
                    {"entry": e.to_dict(), "score": round(s, 4)}
                    for e, s in results
                ],
                "tools_used": ["vector_index"],
            }

        if op == "recent":
            self.require_permission(Permission.MEMORY_READ)
            n = int(payload.get("n", 10))
            entries = self._memory.recent(n)
            return {
                "op": "recent",
                "n": n,
                "entries": [e.to_dict() for e in entries],
                "tools_used": ["memory_store"],
            }

        if op == "clear":
            self.require_permission(Permission.MEMORY_WRITE)
            self._memory.clear()
            return {"op": "clear", "tools_used": ["memory_store"]}

        if op == "keys":
            self.require_permission(Permission.MEMORY_READ)
            return {
                "op": "keys",
                "keys": self._memory.long_keys(),
                "tools_used": ["memory_store"],
            }

        return {"op": op, "error": f"unknown op: {op}"}


__all__ = ["MemoryAgent"]
