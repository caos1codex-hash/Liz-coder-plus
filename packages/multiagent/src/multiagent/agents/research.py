"""ResearchAgent — investiga, busca APIs, resume (Sprint 2.1).

Responsable de:

- consultar documentación
- buscar APIs
- investigar librerías
- generar resúmenes técnicos

Esta implementación base es heurística: si tiene NETWORK_ACCESS y se
le pasa una `url`, intenta un fetch best-effort. Si no, devuelve un
resumen estructurado del `query` recibido.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..base import BaseAgent
from ..enums import Permission

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    """Agente investigador."""

    default_permissions = frozenset({
        Permission.NETWORK_ACCESS,
        Permission.MEMORY_READ,
        Permission.MEMORY_WRITE,
    })
    default_objectives = ["research", "investigate", "summarize", "search"]
    default_tools = ["web_search", "doc_fetch"]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="research", description="Investigates docs, APIs and libraries; produces technical summaries", **kwargs)

    async def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload", {}) or {}
        query = payload.get("query") or payload.get("request") or task.get("name", "")
        topic = payload.get("topic") or self._extract_topic(query)
        depth = payload.get("depth", "standard")

        # Recuperar memoria relacionada (memoria larga con tags del topic).
        related = [
            e.to_dict() for e in self._memory
            if topic.lower() in str(e.value).lower()[:500]
        ][:5]

        # Intentar fetch si hay URL (best-effort).
        url = payload.get("url")
        fetched_content: str | None = None
        if url and self.has_permission(Permission.NETWORK_ACCESS):
            fetched_content = await self._fetch(url)

        # Construir resumen estructurado.
        summary = self._build_summary(query, topic, depth, fetched_content, related)

        self._memory.store(
            key=f"research:{task.get('id', 'unknown')}",
            value=summary,
            tags=["research", topic],
        )

        return {
            "query": query,
            "topic": topic,
            "summary": summary,
            "related_memories": len(related),
            "fetched": fetched_content is not None,
            "tools_used": ["web_search"] + (["doc_fetch"] if fetched_content else []),
            "tokens_prompt": max(1, len(query) // 4),
            "tokens_completion": max(1, len(summary.get("overview", "")) // 4),
        }

    async def _fetch(self, url: str) -> str | None:
        """Fetch HTTP best-effort usando urllib de la stdlib."""
        if not url.startswith(("http://", "https://")):
            return None
        try:
            import urllib.request
            req = urllib.request.Request(
                url, headers={"User-Agent": "LizCoderPlus-ResearchAgent/0.1"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                if resp.status != 200:
                    return None
                raw = resp.read(8192)  # limit to 8KB
                try:
                    return raw.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("ResearchAgent fetch failed for %s: %s", url, exc)
            return None

    def _extract_topic(self, query: str) -> str:
        """Extrae un topic de una query (heurística)."""
        # Quitar stopwords en español/inglés.
        stopwords = {
            "el", "la", "los", "las", "un", "una", "de", "del", "y", "o",
            "the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
        }
        words = re.findall(r"\w+", query.lower())
        significant = [w for w in words if w not in stopwords and len(w) > 2]
        return significant[0] if significant else "general"

    def _build_summary(
        self,
        query: str,
        topic: str,
        depth: str,
        fetched: str | None,
        related: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Construye un resumen estructurado."""
        if fetched:
            overview = (
                f"Resumen basado en fetch de URL: {len(fetched)} bytes "
                f"sobre el topic '{topic}'. Profundidad: {depth}."
            )
        else:
            overview = (
                f"Resumen heurístico sobre '{topic}' para la consulta: "
                f"'{query[:200]}'. No se realizó fetch HTTP."
            )
        return {
            "topic": topic,
            "depth": depth,
            "overview": overview,
            "key_points": [
                f"Topic principal: {topic}",
                f"Fuente: {'HTTP fetch' if fetched else 'memoria/heurística'}",
                f"Memorias relacionadas: {len(related)}",
            ],
            "questions_to_explore": [
                f"¿Qué APIs existen para '{topic}'?",
                f"¿Qué librerías open-source cubren '{topic}'?",
            ],
            "confidence": "low" if not fetched else "medium",
        }


__all__ = ["ResearchAgent"]
