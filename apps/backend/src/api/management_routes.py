"""REST API routes for system management.

Sprint 4 - Phase 2, Sprint 5 - Chat & NVIDIA.

Provides REST endpoints for:
    - /api/models      — List, activate, deactivate AI models
    - /api/sessions    — List, manage conversation sessions
    - /api/tools       — List, enable, disable tools
    - /api/agents      — List agents and their status
    - /api/memory      — Memory management
    - /api/config      — View current configuration
    - /api/chat        — REST chat endpoint (no WebSocket needed)
    - /api/models/discover — Discover available models from providers

These endpoints complement the WebSocket chat endpoint and provide
a management interface for the desktop client.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["management"])


# ---------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------


class ChatRequest(BaseModel):
    """REST chat request model."""

    message: str = Field(..., min_length=1, description="User message")
    session_id: str = Field(default="rest-default", description="Session ID")
    mode: str = Field(default="confirmation", description="Permission mode")
    model: str = Field(default="", description="Override model selection")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=131072)


class PlanTaskRequest(BaseModel):
    """A task within a plan execution request."""

    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    suggested_agent: str = Field(default="llm")
    suggested_tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ExecutePlanRequest(BaseModel):
    """Request to execute a plan as an execution graph."""

    tasks: list[PlanTaskRequest] = Field(..., min_length=1)
    session_id: str = Field(default="plan-default")
    parallel: bool = Field(default=True)


class ExecutePlanResponse(BaseModel):
    """Response from plan execution."""

    status: str
    nodes_total: int
    nodes_completed: int
    nodes_failed: int
    duration_ms: float
    results: list[dict[str, Any]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """REST chat response model."""

    type: str
    content: str
    status: str
    session_id: str
    message_id: str = ""
    agent_name: str = ""
    model: str = ""
    provider: str = ""
    duration_ms: int = 0
    stage_timings: dict[str, int] = {}


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
# Chat (REST)
# ---------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat_rest(request: ChatRequest) -> dict[str, Any]:
    """Send a message and get a response via REST (no WebSocket).

    This endpoint allows clients that don't support WebSocket to
    interact with Liz via standard HTTP POST. The response is
    generated using the full execution pipeline (plan, context
    engine, multi-agent dispatch if applicable).

    Args:
        request: ChatRequest with message, session_id, mode, etc.

    Returns:
        ChatResponse with content, metadata, and timing info.
    """
    from src.api.ws_routes import get_orchestrator

    orch = get_orchestrator()

    # Override model if requested.
    if request.model:
        mm = _find_model_manager(orch)
        if mm is not None:
            try:
                mm.activate(request.model)
            except KeyError:
                pass  # Let the system use whatever is available

    result = await orch.handle_message(
        message=request.message,
        session_id=request.session_id,
        mode=request.mode,
    )

    return result


@router.post("/chat/complete")
async def chat_complete(request: ChatRequest) -> dict[str, Any]:
    """Direct LLM completion without the full pipeline.

    Bypasses the orchestrator pipeline and calls the LLM directly.
    Useful for simple completions, code generation, and translations.
    """
    import os
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    llm_pkg = repo_root / "packages" / "llm" / "src"

    if str(llm_pkg) not in sys.path:
        sys.path.insert(0, str(llm_pkg))

    from src.llm.manager import ModelManager
    from src.llm.models import LLMMessage, MessageRole

    # Find an available model.
    model_id = request.model
    api_key = (
        os.getenv("NVIDIA_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LIZ_AI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("MISTRAL_API_KEY")
        or ""
    )

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="No API key configured. Set NVIDIA_API_KEY, OPENAI_API_KEY, "
                   "DEEPSEEK_API_KEY, or MISTRAL_API_KEY.",
        )

    # Determine provider and base URL based on which key is available.
    if os.getenv("NVIDIA_API_KEY"):
        api_key = os.getenv("NVIDIA_API_KEY")
        base_url = "https://integrate.api.nvidia.com/v1"
        if not model_id:
            model_id = "meta/llama-3.1-405b-instruct"
    elif os.getenv("DEEPSEEK_API_KEY"):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = "https://api.deepseek.com/v1"
        if not model_id:
            model_id = "deepseek-chat"
    elif os.getenv("MISTRAL_API_KEY"):
        api_key = os.getenv("MISTRAL_API_KEY")
        base_url = "https://api.mistral.ai/v1"
        if not model_id:
            model_id = "mistral-large-latest"
    else:
        base_url = os.getenv("LIZ_AI_BASE_URL", "https://api.openai.com/v1")
        if not model_id:
            model_id = "gpt-4o-mini"

    import httpx
    import time

    messages = [
        LLMMessage(role=MessageRole.SYSTEM, content=(
            "Eres Liz, una asistente de IA avanzada. "
            "Responde de forma concisa y útil."
        )),
        LLMMessage(role=MessageRole.USER, content=request.message),
    ]

    start = time.monotonic()
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=120.0,
    ) as client:
        payload = {
            "model": model_id,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        response = await client.post("/chat/completions", json=payload)
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"LLM API error: {response.text[:500]}",
            )
        data = response.json()
        duration_ms = int((time.monotonic() - start) * 1000)

        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        return {
            "type": "message",
            "content": content,
            "status": "completed",
            "session_id": request.session_id,
            "model": data.get("model", model_id),
            "provider": "direct",
            "duration_ms": duration_ms,
        }


# ---------------------------------------------------------------------
# Model Discovery
# ---------------------------------------------------------------------


@router.get("/models/discover")
async def discover_models() -> dict[str, Any]:
    """Discover available models from all configured providers.

    Queries the NVIDIA NIM, DeepSeek, and Mistral APIs for their
    available models. Requires at least one API key to be configured.
    """
    import os
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    llm_pkg = repo_root / "packages" / "llm" / "src"
    if str(llm_pkg) not in sys.path:
        sys.path.insert(0, str(llm_pkg))

    result: dict[str, Any] = {"providers": {}, "total": 0}

    # NVIDIA NIM discovery.
    nvidia_key = os.getenv("NVIDIA_API_KEY", "")
    if nvidia_key:
        try:
            from src.llm.providers.nvidia import NvidiaProvider
            models = await NvidiaProvider.discover_available_models(
                api_key=nvidia_key,
            )
            result["providers"]["nvidia"] = {
                "available": True,
                "models": models,
                "count": len(models),
            }
            result["total"] += len(models)
        except Exception as exc:
            result["providers"]["nvidia"] = {
                "available": False,
                "error": str(exc),
                "count": 0,
            }

    # DeepSeek (no discovery API, list known models).
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
    if deepseek_key:
        result["providers"]["deepseek"] = {
            "available": True,
            "models": [
                {"id": "deepseek-chat", "owned_by": "deepseek"},
                {"id": "deepseek-reasoner", "owned_by": "deepseek"},
                {"id": "deepseek-coder", "owned_by": "deepseek"},
            ],
            "count": 3,
        }
        result["total"] += 3

    # Mistral (no discovery API, list known models).
    mistral_key = os.getenv("MISTRAL_API_KEY", "")
    if mistral_key:
        result["providers"]["mistral"] = {
            "available": True,
            "models": [
                {"id": "mistral-large-latest", "owned_by": "mistral"},
                {"id": "codestral-latest", "owned_by": "mistral"},
                {"id": "pixtral-large-latest", "owned_by": "mistral"},
                {"id": "open-mistral-nemo", "owned_by": "mistral"},
                {"id": "mistral-small-latest", "owned_by": "mistral"},
            ],
            "count": 5,
        }
        result["total"] += 5

    if result["total"] == 0:
        result["message"] = (
            "No API keys configured. Set NVIDIA_API_KEY, DEEPSEEK_API_KEY, "
            "or MISTRAL_API_KEY to discover models."
        )

    return result


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


# ---------------------------------------------------------------------
# Plan Execution (Sprint 6)
# ---------------------------------------------------------------------


@router.post("/plans/execute", response_model=ExecutePlanResponse)
async def execute_plan(req: ExecutePlanRequest):
    """Execute a plan using the DAG-based execution graph.

    Sprint 6: Converts plan tasks into a parallel execution graph
    with dependency tracking. Falls back to sequential execution
    if the execution graph package is unavailable.
    """
    orch = get_orchestrator()
    tasks_data = [t.model_dump() for t in req.tasks]

    result = await orch.execute_plan_as_graph(
        tasks_data,
        session_id=req.session_id,
    )

    return ExecutePlanResponse(
        status=result.get("status", "unknown"),
        nodes_total=result.get("nodes_total", 0),
        nodes_completed=result.get("nodes_completed", 0),
        nodes_failed=result.get("nodes_failed", 0),
        duration_ms=result.get("duration_ms", 0),
        results=result.get("results", []),
    )


__all__ = ["router"]
