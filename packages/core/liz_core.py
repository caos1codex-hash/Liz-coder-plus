"""Liz Coder Plus - Core package (monorepo shim).

This module provides a single-file import surface for the core
package, designed to be imported from sibling monorepo packages
(e.g. apps/backend) without requiring `pip install -e packages/core`.

Usage from the backend:
    from liz_core import Orchestrator, WebSocketManager, ...

Implementation note:
We deliberately AVOID polluting sys.modules with a `src` namespace,
because the backend already has its own `src` package. Instead we use
importlib to load each core submodule under a private alias, then
re-export the symbols.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# Absolute path to packages/core/src and packages/shared/src
_CORE_SRC = Path(__file__).resolve().parent / "src"
_SHARED_SRC = Path(__file__).resolve().parents[1] / "shared" / "src"


def _load_module(name: str, file_path: Path) -> ModuleType:
    """Load a Python module from an explicit file path under a private name.

    The module is registered in sys.modules under `name` so that other
    modules loaded later can `import name` and get the same instance.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_with_path_manipulation() -> dict[str, type]:
    """Load core public classes via temporary sys.path manipulation."""
    saved_src = sys.modules.pop("src", None)
    saved_src_children = {
        k: v for k, v in list(sys.modules.items()) if k.startswith("src.")
    }
    for k in saved_src_children:
        sys.modules.pop(k, None)

    saved_path = list(sys.path)
    sys.path.insert(0, str(_CORE_SRC))
    sys.path.append(str(_SHARED_SRC))

    try:
        from src.agent_router import AgentRouter, EchoAgent
        from src.orchestrator import Orchestrator
        from src.protocols import Agent, Memory, Tool
        from src.session_manager import ChatTurn, SessionManager, SessionState
        from src.ws_connection import WebSocketConnection
        from src.ws_manager import WebSocketManager
    finally:
        # Restore sys.path
        sys.path[:] = saved_path
        # Remove the core's `src` namespace from sys.modules so the
        # backend's `src` continues to work for the rest of the app.
        for k in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
            sys.modules.pop(k, None)
        if saved_src is not None:
            sys.modules["src"] = saved_src
        sys.modules.update(saved_src_children)

    return {
        "AgentRouter": AgentRouter,
        "EchoAgent": EchoAgent,
        "Orchestrator": Orchestrator,
        "Agent": Agent,
        "Memory": Memory,
        "Tool": Tool,
        "ChatTurn": ChatTurn,
        "SessionManager": SessionManager,
        "SessionState": SessionState,
        "WebSocketConnection": WebSocketConnection,
        "WebSocketManager": WebSocketManager,
    }


_symbols = _load_with_path_manipulation()

# Re-export the symbols so `from liz_core import Orchestrator` works.
AgentRouter = _symbols["AgentRouter"]
EchoAgent = _symbols["EchoAgent"]
Orchestrator = _symbols["Orchestrator"]
Agent = _symbols["Agent"]
Memory = _symbols["Memory"]
Tool = _symbols["Tool"]
ChatTurn = _symbols["ChatTurn"]
SessionManager = _symbols["SessionManager"]
SessionState = _symbols["SessionState"]
WebSocketConnection = _symbols["WebSocketConnection"]
WebSocketManager = _symbols["WebSocketManager"]

__all__ = [
    "AgentRouter",
    "EchoAgent",
    "Orchestrator",
    "Agent",
    "Memory",
    "Tool",
    "ChatTurn",
    "SessionManager",
    "SessionState",
    "WebSocketConnection",
    "WebSocketManager",
]
