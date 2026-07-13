"""Core package: orchestrator and event system for Liz Coder Plus."""

from src.agent_router import AgentRouter, EchoAgent  # noqa: F401
from src.orchestrator import Orchestrator  # noqa: F401
from src.protocols import Agent, Memory, Tool  # noqa: F401
from src.session_manager import ChatTurn, SessionManager, SessionState  # noqa: F401
from src.ws_connection import WebSocketConnection  # noqa: F401
from src.ws_manager import WebSocketManager  # noqa: F401

__version__ = "0.1.2"
