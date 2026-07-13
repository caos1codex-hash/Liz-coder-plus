"""FastAPI application entry point.

Sprint 1 - Prompt 1 (Foundation)
This module exposes the ASGI application object `app` that uvicorn loads.
For now it only registers the health endpoint. Future sprints will add
WebSocket endpoints, agent routes, and tool execution.
"""

from __future__ import annotations

from fastapi import FastAPI

from src import __version__

app = FastAPI(
    title="Liz Coder Plus - Backend",
    description="Desktop AI assistant backend (FastAPI + WebSocket).",
    version=__version__,
    contact={"name": "caos1codex-hash"},
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        A dictionary with the service status and version.
    """
    return {"status": "ok", "version": __version__}


@app.get("/status", tags=["system"])
async def status() -> dict[str, str]:
    """Status endpoint with environment information.

    Returns:
        A dictionary describing the current runtime status.
    """
    return {
        "service": "liz-coder-plus-backend",
        "version": __version__,
        "stage": "Sprint 1 - Prompt 1 (Foundation)",
        "state": "scaffolding",
    }


# TODO (Sprint 1 - Prompt 2): add WebSocket endpoint at /ws
# TODO (Sprint 2): integrate core orchestrator
# TODO (Sprint 3): integrate permissions and tool execution
