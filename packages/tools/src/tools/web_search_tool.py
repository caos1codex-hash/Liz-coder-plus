"""Web search tool — search the internet using DuckDuckGo or custom API.

Sprint 5 — Web search capability.

Security:
  - search: LOW permission (read-only, no modifications).
  - Uses DuckDuckGo Lite (HTML scraping) as default, no API key needed.
  - Optionally supports custom search APIs via configuration.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from src.base import BaseTool, PermissionLevel, ToolCategory

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """Tool for web search operations.

    Actions:
      - ``search``: Search the web and return results (LOW).
      - ``fetch_url``: Fetch content from a specific URL (LOW).
    """

    name = "web_search"
    description = "Search the web and fetch URL content."
    version = "1.0.0"
    category = ToolCategory.WEB
    permission_level = PermissionLevel.LOW
    parameters_schema = {
        "required": ["action"],
        "properties": {
            "action": {
                "type": "str",
                "description": "One of: search, fetch_url",
            },
            "query": {
                "type": "str",
                "description": "Search query (for 'search' action).",
            },
            "url": {
                "type": "str",
                "description": "URL to fetch (for 'fetch_url' action).",
            },
            "max_results": {
                "type": "int",
                "description": "Max results to return (default: 5).",
            },
        },
    }

    async def _execute(
        self, params: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a web search or URL fetch."""
        action = params.get("action", "")

        if action == "search":
            return await self._search(
                params.get("query", ""),
                params.get("max_results", 5),
            )
        elif action == "fetch_url":
            return await self._fetch_url(params.get("url", ""))
        else:
            return {
                "success": False,
                "error": f"Unknown action: '{action}'. Use 'search' or 'fetch_url'.",
            }

    async def _search(
        self, query: str, max_results: int = 5
    ) -> dict[str, Any]:
        """Search the web using DuckDuckGo Lite."""
        if not query:
            return {"success": False, "error": "Query is required."}

        try:
            search_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"

            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Liz-Coder-Plus/1.0 (Web Search)",
                },
            ) as client:
                response = await client.get(search_url)
                response.raise_for_status()

                html = response.text
                results = self._parse_ddg_lite(html, max_results)

                return {
                    "success": True,
                    "query": query,
                    "results": results,
                    "count": len(results),
                }
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": "Search timed out. Try again.",
            }
        except Exception as exc:
            logger.exception("Web search failed")
            return {"success": False, "error": f"Search error: {exc}"}

    @staticmethod
    def _parse_ddg_lite(html: str, max_results: int) -> list[dict[str, str]]:
        """Parse DuckDuckGo Lite HTML results."""
        results = []

        # Extract result links and titles from DDG Lite HTML.
        # DDG Lite uses a simple table-based layout.
        link_pattern = re.compile(
            r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>'
            r'\s*(.*?)\s*</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:max_results]):
            # Clean HTML tags from title.
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = (
                re.sub(r"<[^>]+>", "", snippets[i]).strip()
                if i < len(snippets)
                else ""
            )
            if clean_title and url:
                results.append({
                    "title": clean_title,
                    "url": url,
                    "snippet": snippet[:500],
                })

        return results

    async def _fetch_url(self, url: str) -> dict[str, Any]:
        """Fetch content from a specific URL."""
        if not url:
            return {"success": False, "error": "URL is required."}

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Liz-Coder-Plus/1.0 (Web Fetch)",
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")

                # Only return text content.
                if "text" in content_type or "html" in content_type or "json" in content_type:
                    # Truncate large responses.
                    text = response.text[:10000]
                    # Clean HTML if present.
                    if "html" in content_type:
                        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                        text = re.sub(r"<[^>]+>", " ", text)
                        text = re.sub(r"\s+", " ", text).strip()

                    return {
                        "success": True,
                        "url": url,
                        "content": text[:5000],
                        "content_type": content_type,
                        "status_code": response.status_code,
                    }
                else:
                    return {
                        "success": True,
                        "url": url,
                        "content": f"[Binary content: {content_type}]",
                        "content_type": content_type,
                        "status_code": response.status_code,
                    }

        except httpx.TimeoutException:
            return {"success": False, "error": "Fetch timed out."}
        except Exception as exc:
            return {"success": False, "error": f"Fetch error: {exc}"}
