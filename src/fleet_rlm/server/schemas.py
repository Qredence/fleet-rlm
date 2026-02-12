"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fleet_rlm import __version__


class ChatRequest(BaseModel):
    message: str
    docs_path: str | None = None
    trace: bool = False


class ChatResponse(BaseModel):
    assistant_response: str
    trajectory: dict[str, Any] | None = None
    history_turns: int = 0


class HealthResponse(BaseModel):
    ok: bool = True
    version: str = __version__


class ReadyResponse(BaseModel):
    ready: bool
    planner_configured: bool


class TaskRequest(BaseModel):
    task_type: Literal[
        "basic",
        "architecture",
        "api_endpoints",
        "error_patterns",
        "long_context",
        "summarize",
        "custom_tool",
    ]
    question: str = ""
    docs_path: str | None = None
    query: str = ""
    max_iterations: int = 15
    max_llm_calls: int = 30
    timeout: int = 600
    chars: int = 10000
    verbose: bool = True


class TaskResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WSMessage(BaseModel):
    type: Literal["message", "cancel", "command"] = "message"
    content: str = ""
    docs_path: str | None = None
    trace: bool = True
    trace_mode: Literal["compact", "verbose", "off"] | None = None
    # Command dispatch fields (used when type == "command")
    command: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


class WSCommandMessage(BaseModel):
    """Typed model for a WebSocket command message."""

    type: Literal["command"] = "command"
    command: str
    args: dict[str, Any] = Field(default_factory=dict)


class WSCommandResult(BaseModel):
    """Server response for a command dispatch."""

    type: Literal["command_result"] = "command_result"
    command: str
    result: dict[str, Any] = Field(default_factory=dict)
