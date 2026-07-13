"""FastAPI application entry point.

Sprint 1 - Prompt 2
This module exposes the ASGI application object `app` that uvicorn loads.
It registers:
    - REST endpoints /health and /status
    - WebSocket endpoint /ws/chat (see src/api/ws_routes.py)
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src import __version__
from src.api.ws_routes import get_orchestrator, get_ws_manager, router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup + shutdown hooks."""
    # Startup: log orchestrator and ws manager state.
    orch = get_orchestrator()
    ws = get_ws_manager()
    print(f"[lifespan] Liz Coder Plus backend v{__version__} starting")
    print(f"[lifespan] Orchestrator ready, ws_manager={ws.connection_count}")
    yield
    # Shutdown: close any lingering connections.
    print("[lifespan] Backend shutting down")


app = FastAPI(
    title="Liz Coder Plus - Backend",
    description="Desktop AI assistant backend (FastAPI + WebSocket).",
    version=__version__,
    contact={"name": "caos1codex-hash"},
    lifespan=lifespan,
)

# Register routes.
app.include_router(ws_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        A dictionary with the service status and version.
    """
    return {"status": "ok", "version": __version__}


@app.get("/status", tags=["system"])
async def status() -> dict[str, object]:
    """Status endpoint with environment information.

    Returns:
        A dictionary describing the current runtime status, including
        the number of active WebSocket connections.
    """
    ws = get_ws_manager()
    orch = get_orchestrator()
    return {
        "service": "liz-coder-plus-backend",
        "version": __version__,
        "stage": "Sprint 1 - Prompt 2 (WebSocket layer)",
        "state": "operational",
        "ws_connections": ws.connection_count,
        "ws_sessions": ws.list_sessions(),
        "memory_sessions": orch.session_manager.session_count,
        "agents": orch.router.agent_names,
    }


# TODO (Sprint 2): wire persistent memory backend
# TODO (Sprint 3): integrate permissions and tool execution
