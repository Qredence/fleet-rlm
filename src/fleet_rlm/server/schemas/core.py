"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from fleet_rlm import __version__

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]


class ChatRequest(BaseModel):
    message: str
    docs_path: str | None = None
    trace: bool = False
    execution_mode: ExecutionMode = "auto"


class ChatResponse(BaseModel):
    assistant_response: str
    trajectory: dict[str, Any] | None = None
    history_turns: int = 0
    guardrail_warnings: list[str] = Field(default_factory=list)
    effective_max_iters: int | None = None
    delegate_calls_turn: int | None = None
    delegate_fallback_count_turn: int | None = None
    delegate_result_truncated_count_turn: int | None = None
    core_memory_snapshot: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    ok: bool = True
    version: str = __version__


class ReadyResponse(BaseModel):
    ready: bool
    planner_configured: bool
    planner: Literal["ready", "missing"]
    database: Literal["ready", "missing", "disabled", "degraded"]
    database_required: bool
    sandbox_provider: str


class AuthMeResponse(BaseModel):
    tenant_claim: str
    user_claim: str
    email: str | None = None
    name: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None


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
    execution_mode: ExecutionMode = "auto"
    workspace_id: str = "default"
    user_id: str = "anonymous"
    session_id: str | None = None
    # Command dispatch fields (used when type == "command")
    command: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


class WSCommandMessage(BaseModel):
    """Typed model for a WebSocket command message."""

    type: Literal["command"] = "command"
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str = "default"
    user_id: str = "anonymous"
    session_id: str | None = None


class WSCommandResult(BaseModel):
    """Server response for a command dispatch."""

    type: Literal["command_result"] = "command_result"
    command: str
    result: dict[str, Any] = Field(default_factory=dict)


class SessionStateSummary(BaseModel):
    key: str
    workspace_id: str
    user_id: str
    session_id: str | None = None
    history_turns: int = 0
    document_count: int = 0
    memory_count: int = 0
    log_count: int = 0
    artifact_count: int = 0
    updated_at: str | None = None


class SessionStateResponse(BaseModel):
    ok: bool = True
    sessions: list[SessionStateSummary] = Field(default_factory=list)


class RuntimeSettingsSnapshot(BaseModel):
    env_path: str
    keys: list[str] = Field(default_factory=list)
    values: dict[str, str] = Field(default_factory=dict)
    masked_values: dict[str, str] = Field(default_factory=dict)


class RuntimeSettingsUpdateRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettingsUpdateResponse(BaseModel):
    updated: list[str] = Field(default_factory=list)
    env_path: str


class RuntimeConnectivityTestResponse(BaseModel):
    kind: Literal["modal", "lm"]
    ok: bool
    preflight_ok: bool
    checked_at: str
    checks: dict[str, Any] = Field(default_factory=dict)
    guidance: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None


class RuntimeTestCache(BaseModel):
    modal: RuntimeConnectivityTestResponse | None = None
    lm: RuntimeConnectivityTestResponse | None = None


class RuntimeActiveModels(BaseModel):
    planner: str = ""
    delegate: str = ""
    delegate_small: str = ""


class RuntimeStatusResponse(BaseModel):
    app_env: str
    write_enabled: bool
    ready: bool
    active_models: RuntimeActiveModels
    llm: dict[str, Any] = Field(default_factory=dict)
    modal: dict[str, Any] = Field(default_factory=dict)
    tests: RuntimeTestCache
    guidance: list[str] = Field(default_factory=list)


class VolumeTreeNode(BaseModel):
    """A single node in the volume file tree."""

    id: str
    name: str
    path: str
    type: Literal["volume", "directory", "file"]
    children: list["VolumeTreeNode"] = Field(default_factory=list)
    size: int | None = None
    modified_at: str | None = None


class VolumeTreeResponse(BaseModel):
    """Response for the volume tree listing endpoint."""

    volume_name: str
    root_path: str
    nodes: list[VolumeTreeNode]
    total_files: int = 0
    total_dirs: int = 0
    truncated: bool = False


class VolumeFileContentResponse(BaseModel):
    """Response for runtime volume file-content preview endpoint."""

    path: str
    mime: str
    size: int
    content: str
    truncated: bool = False


class TraceFeedbackRequest(BaseModel):
    trace_id: str | None = None
    client_request_id: str | None = None
    is_correct: bool
    comment: str | None = None
    expected_response: str | None = None

    @model_validator(mode="after")
    def validate_trace_lookup_target(self) -> "TraceFeedbackRequest":
        if (self.trace_id or "").strip() or (self.client_request_id or "").strip():
            return self
        raise ValueError("trace_id or client_request_id is required")


class TraceFeedbackResponse(BaseModel):
    ok: bool = True
    trace_id: str
    client_request_id: str | None = None
    feedback_logged: bool = True
    expectation_logged: bool = False
