"""FastAPI application entry point.

Sprint 1 - Prompt 2 (base), Sprint 1.4 (memory integration).

This module exposes the ASGI application object `app` that uvicorn loads.
It registers:
    - REST endpoints /health and /status
    - WebSocket endpoint /ws/chat (see src/api/ws_routes.py)
    - Lifespan hook that initializes persistent memory when configured.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src import __version__
from src.api.ws_routes import get_orchestrator, get_ws_manager, router as ws_router

logger = logging.getLogger(__name__)


async def _initialize_memory(orchestrator: object) -> None:
    """Initialize and attach persistent memory to the orchestrator.

    This function is called during the FastAPI lifespan startup phase.
    It imports the MemoryManager from the memory package, initializes
    it with the configured database path, and attaches it to both
    the SessionManager and the Orchestrator.

    If the memory package or dependencies are not available, this
    function logs a warning and returns without error — the system
    continues to work with RAM-only sessions.
    """
    try:
        # Dynamically load the memory package.
        repo_root = Path(__file__).resolve().parents[4]
        memory_pkg = repo_root / "packages" / "memory"
        if str(memory_pkg) not in sys.path:
            sys.path.insert(0, str(memory_pkg))

        from src.memory.manager import MemoryManager  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "Memory package not available. "
            "Continuing with RAM-only sessions."
        )
        return

    try:
        # Load configuration to get memory settings.
        from src.core.config import load_config

        config = load_config()
        mem_cfg = config.memory

        if not mem_cfg.enabled:
            logger.info("Persistent memory is disabled in configuration")
            return

        # Resolve the database path relative to the project root.
        db_path = mem_cfg.path
        if not Path(db_path).is_absolute():
            db_path = str(repo_root / db_path)

        mm = MemoryManager(max_history=mem_cfg.max_history)
        await mm.initialize(db_path)

        # Attach to orchestrator (which also attaches to SessionManager).
        orchestrator.attach_memory(mm)  # type: ignore[union-attr]

        logger.info(
            "Persistent memory initialized: db=%s, max_history=%d",
            db_path,
            mem_cfg.max_history,
        )
    except Exception:
        logger.exception(
            "Failed to initialize persistent memory. "
            "Continuing with RAM-only sessions."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup + shutdown hooks."""
    orch = get_orchestrator()
    ws = get_ws_manager()

    logger.info("Liz Coder Plus backend v%s starting", __version__)
    logger.info("WebSocket manager: %d connections", ws.connection_count)

    # Initialize persistent memory if configured.
    await _initialize_memory(orch)

    yield

    # Shutdown.
    logger.info("Backend shutting down")


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
        the number of active WebSocket connections and memory sessions.
    """
    ws = get_ws_manager()
    orch = get_orchestrator()
    return {
        "service": "liz-coder-plus-backend",
        "version": __version__,
        "stage": "Sprint 1.4 - Persistent Memory",
        "state": "operational",
        "ws_connections": ws.connection_count,
        "ws_sessions": ws.list_sessions(),
        "memory_sessions": orch.session_manager.session_count,
        "agents": orch.router.agent_names,
    }