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
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src import __version__
from src.api.ws_routes import get_orchestrator, get_ws_manager, router as ws_router
from src.api.management_routes import router as mgmt_router
from src.core.config import load_config
from src.multiagent_init import initialize_multiagent_system

logger = logging.getLogger(__name__)


async def _initialize_llm(orchestrator: object) -> None:
    """Initialize and attach the LLM subsystem to the orchestrator.

    This function loads the ModelManager, registers models from
    configuration, creates the LLMAgent, and wires everything
    into the orchestrator. Gracefully degrades if no API keys
    are configured — the LLMAgent falls back to echo mode.

    Sprint 6: Registers tools and wires tool-use loop to LLMAgent.
    """
    try:
        # Dynamically load the LLM package.
        repo_root = Path(__file__).resolve().parents[4]
        llm_pkg = repo_root / "packages" / "llm" / "src"
        core_pkg = repo_root / "packages" / "core" / "src"
        tools_pkg = repo_root / "packages" / "tools" / "src"

        if str(llm_pkg) not in sys.path:
            sys.path.insert(0, str(llm_pkg))
        if str(core_pkg) not in sys.path:
            sys.path.insert(0, str(core_pkg))
        if str(tools_pkg) not in sys.path:
            sys.path.insert(0, str(tools_pkg))

        from src.llm.manager import ModelManager  # type: ignore[import-untyped]
        from src.llm.models import ModelInfo, ModelProvider, ModelCapabilities, ModelStatus  # type: ignore[import-untyped]
        from src.llm_agent import LLMAgent  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "LLM package not available. "
            "Continuing with echo agent."
        )
        return

    try:
        config = load_config()
        ai_cfg = config.ai

        # Create ModelManager.
        mm = ModelManager(default_model=ai_cfg.model)

        # Register models from environment variables.
        registered_count = 0

        # OpenAI
        openai_key = os.getenv("OPENAI_API_KEY") or os.getenv("LIZ_AI_API_KEY", "")
        if openai_key:
            openai_base = os.getenv("LIZ_AI_BASE_URL", "")
            openai_models = [ai_cfg.model, "gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
            for model_id in openai_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.OPENAI,
                    display_name=model_id,
                    description=f"OpenAI model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=128000, max_output_tokens=16384,
                    ),
                    status=ModelStatus.ACTIVE,
                    base_url=openai_base or "https://api.openai.com/v1",
                    api_key_env="OPENAI_API_KEY",
                ))
                registered_count += 1

        # Anthropic Claude
        claude_key = os.getenv("ANTHROPIC_API_KEY", "")
        if claude_key:
            claude_models = ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-20250414"]
            for model_id in claude_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.ANTHROPIC,
                    display_name=model_id,
                    description=f"Anthropic Claude model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=200000, max_output_tokens=8192,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="ANTHROPIC_API_KEY",
                ))
                registered_count += 1

        # Google Gemini
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if google_key:
            gemini_models = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
            for model_id in gemini_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.GOOGLE,
                    display_name=model_id,
                    description=f"Google Gemini model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=1048576, max_output_tokens=65536,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="GOOGLE_API_KEY",
                ))
                registered_count += 1

        # Ollama (local)
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        ollama_models_env = os.getenv("LIZ_OLLAMA_MODELS", "llama3:8b,qwen2.5:7b").split(",")
        # Register Ollama if the URL is set.
        if os.getenv("LIZ_OLLAMA_ENABLE", "").lower() in ("1", "true", "yes"):
            for model_id in ollama_models_env:
                model_id = model_id.strip()
                if not model_id or model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.OLLAMA,
                    display_name=model_id,
                    description=f"Local Ollama model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=False,
                        context_window=8192, max_output_tokens=4096,
                    ),
                    status=ModelStatus.ACTIVE,
                    base_url=ollama_url,
                ))
                registered_count += 1

        # OpenRouter
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        if openrouter_key:
            openrouter_models_env = os.getenv("LIZ_OPENROUTER_MODELS", "anthropic/claude-3.5-sonnet").split(",")
            for model_id in openrouter_models_env:
                model_id = model_id.strip()
                if not model_id or model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.OPENROUTER,
                    display_name=model_id,
                    description=f"OpenRouter model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=128000, max_output_tokens=16384,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="OPENROUTER_API_KEY",
                    base_url="https://openrouter.ai/api/v1",
                ))
                registered_count += 1

        # NVIDIA NIM — auto-detect free models with the provided API key.
        nvidia_key = os.getenv("NVIDIA_API_KEY", "")
        if nvidia_key:
            from src.llm.providers.nvidia import NvidiaProvider  # type: ignore[import-untyped]
            try:
                available_nvidia = await NvidiaProvider.discover_available_models(
                    api_key=nvidia_key,
                )
                # Register discovered models (prefer large, free models first).
                # Priority list: models known to be free on NVIDIA NIM.
                nvidia_free_priority = [
                    "meta/llama-3.1-405b-instruct",
                    "meta/llama-3.1-70b-instruct",
                    "mistralai/mistral-large-2-instruct",
                    "mistralai/mixtral-8x22b-instruct-v0.1",
                    "nvidia/llama-3.1-nemotron-70b-instruct",
                    "google/gemma-2-27b-it",
                    "meta/llama-3.1-8b-instruct",
                    "nvidia/llama-3.1-nemotron-8b-instruct",
                    "microsoft/phi-3.5-mini-instruct",
                    "google/gemma-2-9b-it",
                    "deepseek-ai/deepseek-coder-6.7b-instruct",
                    "qwen/qwen2.5-72b-instruct",
                ]
                # Get IDs of available models.
                available_ids = [
                    m.get("id", "") for m in available_nvidia
                    if m.get("id", "")
                ]
                logger.info(
                    "NVIDIA NIM: discovered %d models", len(available_ids)
                )
                for model_id in nvidia_free_priority:
                    if model_id in available_ids and model_id not in mm.list_models():
                        mm.register(ModelInfo(
                            model_id=model_id,
                            provider=ModelProvider.NVIDIA,
                            display_name=model_id.split("/")[-1],
                            description=f"NVIDIA NIM (free): {model_id}",
                            capabilities=ModelCapabilities(
                                chat=True, streaming=True, function_calling=True,
                                context_window=131072, max_output_tokens=4096,
                            ),
                            status=ModelStatus.ACTIVE,
                            api_key_env="NVIDIA_API_KEY",
                            base_url="https://integrate.api.nvidia.com/v1",
                        ))
                        registered_count += 1
                # Register any additional discovered models not in priority list.
                for model_id in available_ids:
                    if model_id not in mm.list_models():
                        mm.register(ModelInfo(
                            model_id=model_id,
                            provider=ModelProvider.NVIDIA,
                            display_name=model_id.split("/")[-1],
                            description=f"NVIDIA NIM: {model_id}",
                            capabilities=ModelCapabilities(
                                chat=True, streaming=True, function_calling=True,
                                context_window=131072, max_output_tokens=4096,
                            ),
                            status=ModelStatus.ACTIVE,
                            api_key_env="NVIDIA_API_KEY",
                            base_url="https://integrate.api.nvidia.com/v1",
                        ))
                        registered_count += 1
            except Exception:
                logger.warning(
                    "Failed to discover NVIDIA NIM models. "
                    "Will use fallback models.",
                    exc_info=True,
                )
                # Register fallback NVIDIA models if discovery fails.
                nvidia_fallback_models = [
                    "meta/llama-3.1-405b-instruct",
                    "meta/llama-3.1-70b-instruct",
                    "mistralai/mistral-large-2-instruct",
                    "qwen/qwen2.5-72b-instruct",
                ]
                for model_id in nvidia_fallback_models:
                    if model_id not in mm.list_models():
                        mm.register(ModelInfo(
                            model_id=model_id,
                            provider=ModelProvider.NVIDIA,
                            display_name=model_id.split("/")[-1],
                            description=f"NVIDIA NIM (free): {model_id}",
                            capabilities=ModelCapabilities(
                                chat=True, streaming=True, function_calling=True,
                                context_window=131072, max_output_tokens=4096,
                            ),
                            status=ModelStatus.ACTIVE,
                            api_key_env="NVIDIA_API_KEY",
                            base_url="https://integrate.api.nvidia.com/v1",
                        ))
                        registered_count += 1

        # DeepSeek
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            deepseek_models = ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"]
            for model_id in deepseek_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.DEEPSEEK,
                    display_name=model_id,
                    description=f"DeepSeek model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=65536, max_output_tokens=8192,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="DEEPSEEK_API_KEY",
                    base_url="https://api.deepseek.com/v1",
                ))
                registered_count += 1

        # Mistral AI
        mistral_key = os.getenv("MISTRAL_API_KEY", "")
        if mistral_key:
            mistral_models = [
                "mistral-large-latest",
                "codestral-latest",
                "pixtral-large-latest",
                "open-mistral-nemo",
                "mistral-small-latest",
            ]
            for model_id in mistral_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.MISTRAL,
                    display_name=model_id,
                    description=f"Mistral AI model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=131072, max_output_tokens=8192,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="MISTRAL_API_KEY",
                    base_url="https://api.mistral.ai/v1",
                ))
                registered_count += 1

        # GLM (ZhipuAI)
        glm_key = os.getenv("GLM_API_KEY", "")
        if glm_key:
            glm_models = [
                "glm-4-plus",
                "glm-4-long",
                "glm-4-flash",
                "glm-4-air",
                "glm-4v-plus",
            ]
            for model_id in glm_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.GLM,
                    display_name=model_id,
                    description=f"GLM (ZhipuAI) model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=128000, max_output_tokens=4096,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="GLM_API_KEY",
                    base_url="https://open.bigmodel.cn/api/paas/v4",
                ))
                registered_count += 1

        # Qwen (Alibaba Cloud)
        qwen_key = os.getenv("QWEN_API_KEY", "")
        if qwen_key:
            qwen_models = [
                "qwen-max",
                "qwen-plus",
                "qwen-turbo",
                "qwen-long",
                "qwen2.5-coder-32b-instruct",
                "qwen-vl-max",
            ]
            for model_id in qwen_models:
                if model_id in mm.list_models():
                    continue
                mm.register(ModelInfo(
                    model_id=model_id,
                    provider=ModelProvider.QWEN,
                    display_name=model_id,
                    description=f"Qwen (Alibaba Cloud) model: {model_id}",
                    capabilities=ModelCapabilities(
                        chat=True, streaming=True, function_calling=True,
                        context_window=131072, max_output_tokens=8192,
                    ),
                    status=ModelStatus.ACTIVE,
                    api_key_env="QWEN_API_KEY",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                ))
                registered_count += 1

        # Initialize the ModelManager.
        await mm.initialize()

        # Sprint 6: Register tools from the tools package.
        tools_registered = 0
        try:
            from src.tools.terminal_tool import TerminalTool  # type: ignore[import-untyped]
            from src.tools.file_tool import FileTool  # type: ignore[import-untyped]
            from src.tools.system_tool import SystemTool  # type: ignore[import-untyped]
            from src.tools.code_execution_tool import CodeExecutionTool  # type: ignore[import-untyped]
            from src.tools.web_search_tool import WebSearchTool  # type: ignore[import-untyped]
            from src.tools.file_search_tool import FileSearchTool  # type: ignore[import-untyped]

            tool_classes = [
                TerminalTool,
                FileTool,
                SystemTool,
                CodeExecutionTool,
                WebSearchTool,
                FileSearchTool,
            ]
            for tool_cls in tool_classes:
                try:
                    tool_instance = tool_cls()
                    orchestrator.register_tool(tool_instance)
                    tools_registered += 1
                    logger.debug("Registered tool: %s", tool_instance.name)
                except Exception:
                    logger.exception("Failed to register tool %s", tool_cls.__name__)
        except ImportError as exc:
            logger.warning("Tools package not available: %s", exc)

        # Create a tool bridge for the LLMAgent.
        # The bridge connects the ToolExecutor to the orchestrator's tools.
        class ToolBridge:
            """Bridge between ToolExecutor and Orchestrator's registered tools."""
            def __init__(self, tool_executor, orchestrator_tools):
                self._tool_executor = tool_executor
                self._orchestrator_tools = orchestrator_tools

            async def execute(self, tool, params, *, session_id=""):
                return await self._tool_executor.execute(
                    tool, params, session_id=session_id
                )

        tool_bridge = ToolBridge(
            tool_executor=orchestrator.tool_executor,
            orchestrator_tools=orchestrator._tools,
        )

        # Create and register the LLMAgent with tool support.
        llm_agent = LLMAgent(
            model_manager=mm,
            default_model=ai_cfg.model,
            temperature=ai_cfg.temperature,
            max_tokens=ai_cfg.max_tokens,
            tool_executor=tool_bridge,
        )
        await llm_agent.initialize()
        orchestrator.register_agent(llm_agent)

        logger.info(
            "LLM subsystem initialized: %d model(s) registered, "
            "agent='%s', default_model='%s', %d tool(s) registered",
            registered_count,
            llm_agent.name,
            ai_cfg.model,
            tools_registered,
        )

    except Exception:
        logger.exception(
            "Failed to initialize LLM subsystem. "
            "Continuing with echo agent."
        )


async def _initialize_advanced_systems(orchestrator: object) -> None:
    """Initialize Planner, ContextEngine, PlanExecutor, and Recovery.

    Sprint 4 - Phase 3: Wire the advanced systems into the orchestrator
    so the full pipeline (plan -> context -> execute -> recover) works.
    """
    try:
        repo_root = Path(__file__).resolve().parents[4]
        core_pkg = repo_root / "packages" / "core" / "src"

        if str(core_pkg) not in sys.path:
            sys.path.insert(0, str(core_pkg))

        from src.planner import Planner
        from src.context_engine.context_engine import ContextEngine
        from src.plan_executor import PlanExecutor
        from src.recovery import RecoveryManager, CheckpointManager
        from src.plan_tracking import PlanExecutionTracker
    except ImportError as exc:
        logger.warning("Advanced systems not available: %s", exc)
        return

    try:
        # 1. Planner with LLM support.
        planner = Planner(event_bus=orchestrator.event_bus)
        # Attach ModelManager for LLM-backed planning if available.
        llm_agent_ref = orchestrator._agents.get("llm")
        if llm_agent_ref and hasattr(llm_agent_ref, 'model_manager'):
            planner.attach_model_manager(llm_agent_ref.model_manager)
        orchestrator.attach_planner(planner)
        logger.info("Planner attached to Orchestrator")

        # 2. ContextEngine.
        ctx_engine = ContextEngine(event_bus=orchestrator.event_bus)
        orchestrator.attach_context_engine(ctx_engine)
        logger.info("ContextEngine attached to Orchestrator")

        # 3. PlanExecutor.
        plan_executor = PlanExecutor(
            orchestrator=orchestrator,
            event_bus=orchestrator.event_bus,
        )
        orchestrator.attach_plan_executor(plan_executor)
        logger.info("PlanExecutor attached to Orchestrator")

        # 4. PlanExecutionTracker.
        tracker = PlanExecutionTracker(
            orchestrator=orchestrator,
            event_bus=orchestrator.event_bus,
        )
        orchestrator.attach_plan_tracker(tracker)
        logger.info("PlanExecutionTracker attached to Orchestrator")

        # 5. Recovery (if memory is available).
        if orchestrator._memory is not None and orchestrator._memory.db is not None:
            cpm = CheckpointManager(orchestrator._memory.db)
            await cpm.ensure_table()
            cpm.attach_event_bus(orchestrator.event_bus)

            recovery = RecoveryManager(
                task_manager=orchestrator,
                workflow_manager=orchestrator,
                checkpoint_manager=cpm,
                event_bus=orchestrator.event_bus,
            )
            # Store for future use.
            orchestrator._recovery = recovery
            logger.info("RecoveryManager initialized")

        logger.info("Advanced systems initialized successfully")

    except Exception:
        logger.exception(
            "Failed to initialize advanced systems. "
            "Continuing with basic functionality."
        )


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

    # Initialize LLM subsystem (ModelManager + LLMAgent).
    await _initialize_llm(orch)

    # Initialize the orchestrator (agents, event bus, etc.).
    await orch.initialize()

    # Initialize Planner, ContextEngine, and PlanExecutor.
    await _initialize_advanced_systems(orch)

    # Initialize multiagent system (7 specialized agents).
    await initialize_multiagent_system(orch)

    # Initialize persistent memory if configured.
    await _initialize_memory(orch)

    yield

    # Shutdown.
    logger.info("Backend shutting down")
    await orch.shutdown()


app = FastAPI(
    title="Liz Coder Plus - Backend",
    description="Desktop AI assistant backend (FastAPI + WebSocket).",
    version=__version__,
    contact={"name": "caos1codex-hash"},
    lifespan=lifespan,
)

# Register routes.
app.include_router(ws_router)
app.include_router(mgmt_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        A dictionary with the service status and version.
    """
    return {"status": "ok", "version": __version__}


@app.get("/status", tags=["system"])
async def status() -> dict[str, object]:
    """Detailed system status endpoint.

    Returns real-time information about:
    - Service version and sprint stage
    - WebSocket connections and sessions
    - Registered agents with capabilities
    - Available LLM models grouped by provider
    - Active tools
    - Memory system status
    - Planning and context engine status
    """
    orch = get_orchestrator()
    ws = get_ws_manager()

    # Build agent info.
    agent_info = []
    for name in orch.router.agent_names:
        agent = orch.router.get_agent(name)
        if agent is not None:
            agent_info.append({
                "name": name,
                "type": type(agent).__name__,
                "capabilities": getattr(agent, "capabilities", []),
            })
        else:
            agent_info.append({"name": name, "type": "unknown"})

    # Build model info from ModelManager if available.
    model_info: list[dict[str, object]] = []
    llm_agent = orch._agents.get("llm")
    if llm_agent and hasattr(llm_agent, "model_manager") and llm_agent.model_manager:
        mm = llm_agent.model_manager
        models_by_provider: dict[str, list[dict[str, str]]] = {}
        for m in mm.list_models():
            provider = m.provider.value
            if provider not in models_by_provider:
                models_by_provider[provider] = []
            models_by_provider[provider].append({
                "id": m.model_id,
                "status": m.status.value,
                "ctx_window": str(m.capabilities.context_window),
            })
        for provider, models in sorted(models_by_provider.items()):
            model_info.append({
                "provider": provider,
                "models": models,
                "default": mm.default_model,
            })

    # Build tool info.
    tool_names = list(orch._tools.keys()) if hasattr(orch, "_tools") else []

    # Memory info.
    memory_status = "disabled"
    memory_sessions = 0
    if orch._memory is not None and orch._memory.is_initialized:
        memory_status = "active"
        memory_sessions = len(orch._memory._cache)

    # Planner / ContextEngine status.
    planner_status = "attached" if getattr(orch, "_planner", None) else "none"
    context_engine_status = "attached" if getattr(orch, "_context_engine", None) else "none"
    plan_executor_status = "attached" if getattr(orch, "_plan_executor", None) else "none"
    recovery_status = "attached" if getattr(orch, "_recovery", None) else "none"

    return {
        "service": "liz-coder-plus-backend",
        "version": __version__,
        "stage": "Sprint 7 - System Hardening & Production Readiness",
        "state": "operational",
        "websocket": {
            "connections": ws.connection_count,
            "sessions": ws.list_sessions(),
        },
        "memory": {
            "status": memory_status,
            "sessions_in_cache": memory_sessions,
        },
        "agents": agent_info,
        "models": model_info,
        "tools": tool_names,
        "pipeline": {
            "planner": planner_status,
            "context_engine": context_engine_status,
            "plan_executor": plan_executor_status,
            "recovery": recovery_status,
        },
    }