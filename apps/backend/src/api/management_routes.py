"""REST API routes for system management.

Sprint 4 - Phase 2.

Provides REST endpoints for:
    - /api/models      — List, activate, deactivate AI models
    - /api/sessions    — List, manage conversation sessions
    - /api/tools       — List, enable, disable tools
    - /api/agents      — List agents and their status
    - /api/memory      — Memory management
    - /api/config      — View current configuration

These endpoints complement the WebSocket chat endpoint and provide
a management interface for the desktop client.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["management"])


# ---------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """List all registered AI models with their status."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    result: dict[str, Any] = {
        "models": [],
        "default_model": "",
        "total": 0,
    }

    # Check for ModelManager.
    model_manager = getattr(orch, "_model_manager", None) or getattr(
        orch, "planner", None
    )
    if model_manager is None:
        # Check if the LLM agent has one.
        for agent in orch._agents.values():
            mm = getattr(agent, "_model_manager", None)
            if mm is not None:
                model_manager = mm
                break

    if model_manager is not None:
        models = model_manager.list_models()
        result["models"] = [m.to_dict() for m in models]
        result["default_model"] = model_manager.default_model
        result["total"] = len(models)
    else:
        result["message"] = "No ModelManager initialized. Configure API keys."

    return result


@router.post("/models/{model_id}/activate")
async def activate_model(model_id: str) -> dict[str, Any]:
    """Activate a registered model."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    model_manager = _find_model_manager(orch)

    if model_manager is None:
        raise HTTPException(
            status_code=503, detail="ModelManager not available"
        )

    try:
        model_manager.activate(model_id)
        return {
            "success": True,
            "model_id": model_id,
            "default_model": model_manager.default_model,
        }
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Model '{model_id}' not found"
        )


@router.post("/models/{model_id}/deactivate")
async def deactivate_model(model_id: str) -> dict[str, Any]:
    """Deactivate a registered model."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    model_manager = _find_model_manager(orch)

    if model_manager is None:
        raise HTTPException(
            status_code=503, detail="ModelManager not available"
        )

    try:
        model_manager.deactivate(model_id)
        return {"success": True, "model_id": model_id}
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Model '{model_id}' not found"
        )


@router.get("/models/{model_id}")
async def get_model(model_id: str) -> dict[str, Any]:
    """Get details of a specific model."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    model_manager = _find_model_manager(orch)

    if model_manager is None:
        raise HTTPException(
            status_code=503, detail="ModelManager not available"
        )

    model = model_manager.get(model_id)
    if model is None:
        raise HTTPException(
            status_code=404, detail=f"Model '{model_id}' not found"
        )

    return model.to_dict()


# ---------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------


@router.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """List all active conversation sessions."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    sessions = orch.session_manager.list_sessions()

    session_details = []
    for sid in sessions:
        snapshot = orch.session_manager.snapshot(sid)
        if snapshot is not None:
            session_details.append({
                "session_id": sid,
                "turn_count": len(snapshot.get("history", [])),
                "user_language": snapshot.get("user_language", "es"),
                "last_mode": snapshot.get("last_mode", "confirmation"),
                "created_at": snapshot.get("created_at", ""),
                "last_active_at": snapshot.get("last_active_at", ""),
            })

    return {
        "total": len(session_details),
        "sessions": session_details,
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """Get details of a specific session."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    snapshot = orch.session_manager.snapshot(session_id)

    if snapshot is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found"
        )

    return snapshot


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """Delete a conversation session and its history."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    deleted = await orch.session_manager.drop(session_id)

    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found"
        )

    return {"success": True, "session_id": session_id}


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str, limit: int = 50
) -> dict[str, Any]:
    """Get conversation history for a session."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    history = orch.session_manager.history(session_id, limit=limit)

    if not history:
        snapshot = orch.session_manager.snapshot(session_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{session_id}' not found",
            )

    return {
        "session_id": session_id,
        "count": len(history),
        "history": [
            {
                "role": t.role,
                "content": t.content,
                "timestamp": t.timestamp.isoformat(),
                "metadata": t.metadata,
            }
            for t in history
        ],
    }


# ---------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------


@router.get("/agents")
async def list_agents() -> dict[str, Any]:
    """List all registered agents with their status."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    return {
        "total": len(orch._agents),
        "agents": orch.agent_status(),
        "default_agent": orch.router.default_agent.name,
        "router_agents": orch.router.agent_names,
    }


@router.get("/agents/{agent_name}")
async def get_agent(agent_name: str) -> dict[str, Any]:
    """Get details of a specific agent."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    status = orch.agent_status()

    if agent_name not in status:
        raise HTTPException(
            status_code=404, detail=f"Agent '{agent_name}' not found"
        )

    return status[agent_name]


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """List all registered tools."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    tools_list = []
    for name, tool in orch._tools.items():
        tool_info: dict[str, Any] = {"name": name}
        if hasattr(tool, "metadata"):
            tool_info.update(tool.metadata)
        elif hasattr(tool, "description"):
            tool_info["description"] = tool.description
        elif hasattr(tool, "category"):
            tool_info["category"] = str(tool.category)
        tools_list.append(tool_info)

    return {
        "total": len(tools_list),
        "tools": tools_list,
    }


# ---------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------


@router.get("/memory/status")
async def memory_status() -> dict[str, Any]:
    """Get memory system status."""
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()
    has_memory = orch._memory is not None

    result: dict[str, Any] = {
        "enabled": has_memory,
        "initialized": False,
        "sessions_stored": 0,
    }

    if has_memory and orch._memory is not None:
        result["initialized"] = orch._memory.is_initialized
        result["max_history"] = orch._memory.max_history

    return result


# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Get current configuration (without sensitive data)."""
    from src.core.config import load_config

    config = load_config()
    return {
        "version": config.version,
        "environment": config.environment,
        "ai": {
            "provider": config.ai.provider,
            "model": config.ai.model,
            "temperature": config.ai.temperature,
            "max_tokens": config.ai.max_tokens,
            "models": config.ai.extra.get(
                "models", {}
            ) if config.ai.extra else {},
        },
        "permissions": {
            "mode": config.permissions.mode,
        },
        "backend": {
            "host": config.backend.host,
            "port": config.backend.port,
        },
        "memory": {
            "enabled": config.memory.enabled,
            "max_history": config.memory.max_history,
        },
    }


# ---------------------------------------------------------------------
# System
# ---------------------------------------------------------------------


@router.get("/system/info")
async def system_info() -> dict[str, Any]:
    """Get comprehensive system information."""
    from src.api.ws_routes import get_orchestrator, get_ws_manager
    from src import __version__

    orch = get_orchestrator()
    ws = get_ws_manager()

    info: dict[str, Any] = {
        "version": __version__,
        "service": "liz-coder-plus",
        "orchestrator": {
            "initialized": orch.is_initialized,
            "agents": len(orch._agents),
            "tools": len(orch._tools),
            "sessions": orch.session_manager.session_count,
            "has_memory": orch._memory is not None,
            "has_planner": orch.planner is not None,
            "has_context_engine": orch.context_engine is not None,
            "has_plan_executor": orch.plan_executor is not None,
        },
        "websocket": {
            "connections": ws.connection_count,
            "sessions": ws.list_sessions(),
        },
        "agents": orch.agent_status(),
    }

    # Add model manager info if available.
    mm = _find_model_manager(orch)
    if mm is not None:
        info["models"] = mm.summary()

    return info


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _find_model_manager(orch: Any) -> Any:
    """Find the ModelManager from the orchestrator's agents."""
    # Check for stored model manager.
    mm = getattr(orch, "_model_manager", None)
    if mm is not None:
        return mm

    # Check LLM agents.
    for agent in orch._agents.values():
        mm = getattr(agent, "_model_manager", None)
        if mm is not None:
            return mm

    return None


__all__ = ["router"]
