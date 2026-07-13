"""Shared Pydantic models.

These models are the canonical shapes used when data crosses a module
boundary (API <-> backend, agent <-> orchestrator, etc.). They use
Pydantic v2 for validation and JSON (de)serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.enums import AgentRole, MessageStatus


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    id: str
    session_id: str
    role: AgentRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: MessageStatus = MessageStatus.COMPLETED


class ChatRequest(BaseModel):
    """Request payload for sending a message to the assistant."""

    session_id: str
    content: str


class ChatResponse(BaseModel):
    """Response payload returned after processing a user message."""

    session_id: str
    message: ChatMessage
    metadata: dict[str, str] = Field(default_factory=dict)


class ToolExecutionRequest(BaseModel):
    """Request to execute a tool."""

    tool_name: str
    params: dict[str, str] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    """Result of a tool execution."""

    tool_name: str
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: int = 0


class PermissionPrompt(BaseModel):
    """Prompt shown to the user when an action requires confirmation."""

    tool_name: str
    description: str
    params: dict[str, str] = Field(default_factory=dict)
    mode: Literal["Confirmation", "Automatic"] = "Confirmation"


class HealthStatus(BaseModel):
    """Health check response."""

    status: Literal["ok", "degraded", "down"] = "ok"
    version: str = "0.1.1"
