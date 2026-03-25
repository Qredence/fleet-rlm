"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from fleet_rlm import __version__

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]
RuntimeMode = Literal["modal_chat", "daytona_pilot"]
VolumeProvider = Literal["modal", "daytona"]


class ChatRequest(BaseModel):
    """Request body for the legacy one-shot chat HTTP endpoint."""

    message: str = Field(description="User message to send to the runtime.")
    docs_path: str | None = Field(
        default=None,
        description="Optional local documentation path to preload before the chat turn.",
    )
    trace: bool = Field(
        default=False,
        description="Whether to include trace-oriented reasoning details in the response.",
    )
    execution_mode: ExecutionMode = Field(
        default="auto",
        description="Modal-only execution mode hint for the chat turn.",
    )


class ChatResponse(BaseModel):
    """Response body for the legacy one-shot chat HTTP endpoint."""

    assistant_response: str = Field(
        description="Final assistant response text returned by the runtime."
    )
    trajectory: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured trajectory details describing the executed reasoning path.",
    )
    history_turns: int = Field(
        default=0,
        description="Number of prior conversation turns included in the runtime state.",
    )
    guardrail_warnings: list[str] = Field(
        default_factory=list,
        description="Warnings raised by runtime guardrails while processing the request.",
    )
    effective_max_iters: int | None = Field(
        default=None,
        description="Resolved maximum iteration count used for the turn, when available.",
    )
    delegate_calls_turn: int | None = Field(
        default=None,
        description="Number of delegate-model calls issued while completing the turn.",
    )
    delegate_fallback_count_turn: int | None = Field(
        default=None,
        description="Number of times delegate execution fell back to a backup path during the turn.",
    )
    delegate_result_truncated_count_turn: int | None = Field(
        default=None,
        description="Number of delegate results truncated before returning the response.",
    )
    core_memory_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="Optional snapshot of core memory state after the turn completed.",
    )


class HealthResponse(BaseModel):
    """Response body for the lightweight health endpoint."""

    ok: bool = Field(
        default=True, description="Whether the service reports itself as healthy."
    )
    version: str = Field(
        default=__version__,
        description="Package version currently serving the API.",
    )


class ReadyResponse(BaseModel):
    """Response body for the readiness endpoint."""

    ready: bool = Field(description="Whether critical startup dependencies are ready.")
    planner_configured: bool = Field(
        description="Whether a planner model is currently configured and available."
    )
    planner: Literal["ready", "missing"] = Field(
        description="Planner readiness classification."
    )
    database: Literal["ready", "missing", "disabled", "degraded"] = Field(
        description="Database readiness classification for persistence-backed features."
    )
    database_required: bool = Field(
        description="Whether the current server configuration requires database availability."
    )
    sandbox_provider: str = Field(
        description="Active sandbox backend selected for runtime execution."
    )


class AuthMeResponse(BaseModel):
    """Resolved identity payload returned to authenticated clients."""

    tenant_claim: str = Field(
        description="Tenant or workspace claim resolved from auth."
    )
    user_claim: str = Field(description="User claim resolved from auth.")
    email: str | None = Field(
        default=None,
        description="User email address when the auth provider returned one.",
    )
    name: str | None = Field(
        default=None,
        description="Display name returned by the auth provider, when available.",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Persisted control-plane tenant identifier for admitted Entra users.",
    )
    user_id: str | None = Field(
        default=None,
        description="Persisted control-plane user identifier for admitted Entra users.",
    )


class TaskRequest(BaseModel):
    """Request body for the legacy task-style HTTP endpoint."""

    task_type: Literal[
        "basic",
        "architecture",
        "api_endpoints",
        "error_patterns",
        "long_context",
        "summarize",
        "custom_tool",
    ] = Field(description="Task template used by the legacy task endpoint.")
    question: str = Field(
        default="", description="Primary question or instruction to answer."
    )
    docs_path: str | None = Field(
        default=None,
        description="Optional local documentation path to preload before task execution.",
    )
    query: str = Field(
        default="", description="Auxiliary query string used by some task modes."
    )
    max_iterations: int = Field(
        default=15,
        description="Maximum reasoning iterations allowed for the task execution.",
    )
    max_llm_calls: int = Field(
        default=30,
        description="Maximum total language-model calls allowed for the task execution.",
    )
    timeout: int = Field(
        default=600,
        description="Maximum task runtime in seconds before the server aborts execution.",
    )
    chars: int = Field(
        default=10000,
        description="Maximum character budget used by legacy long-context helpers.",
    )
    verbose: bool = Field(
        default=True,
        description="Whether verbose execution details should be included in the result payload.",
    )


class TaskResponse(BaseModel):
    """Response body for the legacy task-style HTTP endpoint."""

    ok: bool = Field(
        default=True, description="Whether the task completed successfully."
    )
    result: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured result payload returned by the task executor.",
    )
    error: str | None = Field(
        default=None,
        description="Error message returned when the task did not complete successfully.",
    )


class WSMessage(BaseModel):
    """Typed websocket payload for chat, cancel, and command frames."""

    type: Literal["message", "cancel", "command"] = Field(
        default="message",
        description="Websocket frame type.",
    )
    content: str = Field(
        default="", description="Primary chat content for message frames."
    )
    docs_path: str | None = Field(
        default=None,
        description="Optional local documentation path to preload before execution.",
    )
    trace: bool = Field(
        default=True,
        description="Whether trace-oriented streaming events should be emitted for the turn.",
    )
    trace_mode: Literal["compact", "verbose", "off"] | None = Field(
        default=None,
        description="Optional trace verbosity override for the websocket session.",
    )
    execution_mode: ExecutionMode = Field(
        default="auto",
        description="Modal-only execution mode hint for websocket message frames.",
    )
    runtime_mode: RuntimeMode = Field(
        default="modal_chat",
        description="Top-level runtime mode selected for the websocket turn.",
    )
    repo_url: str | None = Field(
        default=None,
        description="Repository URL to attach to Daytona pilot runs.",
    )
    repo_ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit to checkout for Daytona pilot runs.",
    )
    context_paths: list[str] | None = Field(
        default=None,
        description="Optional repository paths to prioritize as context for Daytona pilot runs.",
    )
    batch_concurrency: int | None = Field(
        default=None,
        description="Optional Daytona concurrency hint for batched repository work.",
    )
    workspace_id: str = Field(
        default="default",
        description="Workspace identifier used to scope server-side session state.",
    )
    user_id: str = Field(
        default="anonymous",
        description="User identifier used to scope server-side session state.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session identifier for restoring an existing websocket session.",
    )
    # Command dispatch fields (used when type == "command")
    command: str = Field(
        default="", description="Command name when `type` is `command`."
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Command arguments when `type` is `command`.",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_daytona_message_contract(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw

        message_type = str(raw.get("type", "message") or "message").strip()
        runtime_mode = str(
            raw.get("runtime_mode", "modal_chat") or "modal_chat"
        ).strip()

        if (
            message_type == "message"
            and runtime_mode == "daytona_pilot"
            and raw.get("max_depth") is not None
        ):
            raise PydanticCustomError(
                "daytona_max_depth_removed",
                "Daytona websocket requests no longer accept max_depth; use the "
                "server-configured recursion depth.",
            )

        if (
            message_type == "message"
            and runtime_mode == "daytona_pilot"
            and str(raw.get("repo_ref", "") or "").strip()
            and not str(raw.get("repo_url", "") or "").strip()
        ):
            raise PydanticCustomError(
                "daytona_repo_ref_requires_repo",
                "Daytona repo_ref requires repo_url.",
            )

        return raw


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
    """Lightweight summary of a persisted or active chat session."""

    key: str = Field(description="Stable in-memory session key used by the server.")
    workspace_id: str = Field(description="Workspace identifier owning the session.")
    user_id: str = Field(description="User identifier owning the session.")
    session_id: str | None = Field(
        default=None,
        description="Optional explicit session identifier when one has been assigned.",
    )
    history_turns: int = Field(
        default=0,
        description="Number of conversation turns currently stored in session history.",
    )
    document_count: int = Field(
        default=0,
        description="Number of loaded document entries attached to the session state.",
    )
    memory_count: int = Field(
        default=0,
        description="Number of persisted memory items in the session manifest.",
    )
    log_count: int = Field(
        default=0,
        description="Number of execution log entries in the session manifest.",
    )
    artifact_count: int = Field(
        default=0,
        description="Number of artifacts currently tracked in the session manifest.",
    )
    updated_at: str | None = Field(
        default=None,
        description="Last updated timestamp recorded in the session manifest, when available.",
    )


class SessionStateResponse(BaseModel):
    """Response body for the session-state summary endpoint."""

    ok: bool = Field(
        default=True,
        description="Whether the session-state query completed successfully.",
    )
    sessions: list[SessionStateSummary] = Field(
        default_factory=list,
        description="Active or restored session summaries currently known to the server.",
    )


class RuntimeSettingsSnapshot(BaseModel):
    """Current runtime settings snapshot returned by the Settings API."""

    env_path: str = Field(
        description="Filesystem path to the environment file being edited."
    )
    keys: list[str] = Field(
        default_factory=list,
        description="Ordered list of runtime setting keys surfaced by the Settings API.",
    )
    values: dict[str, str] = Field(
        default_factory=dict,
        description="Unmasked runtime setting values that are safe to return directly.",
    )
    masked_values: dict[str, str] = Field(
        default_factory=dict,
        description="Masked secret values returned for display-only settings fields.",
    )


class RuntimeSettingsUpdateRequest(BaseModel):
    """Patch body for runtime setting updates."""

    updates: dict[str, Any] = Field(
        default_factory=dict,
        description="Mapping of allowlisted runtime setting keys to their new values.",
    )


class RuntimeSettingsUpdateResponse(BaseModel):
    """Result payload after runtime settings are persisted and hot-applied."""

    updated: list[str] = Field(
        default_factory=list,
        description="Runtime setting keys that were successfully updated.",
    )
    env_path: str = Field(
        description="Filesystem path to the environment file that was updated."
    )


class RuntimeConnectivityTestResponse(BaseModel):
    """Result payload for runtime connectivity and preflight diagnostics."""

    kind: Literal["modal", "lm", "daytona"] = Field(
        description="Runtime subsystem that was tested."
    )
    ok: bool = Field(
        description="Whether the connectivity test completed successfully."
    )
    preflight_ok: bool = Field(
        description="Whether prerequisite configuration checks passed."
    )
    checked_at: str = Field(description="UTC timestamp when the test completed.")
    checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured boolean or value checks collected during the test run.",
    )
    guidance: list[str] = Field(
        default_factory=list,
        description="Human-readable remediation steps when the test did not pass cleanly.",
    )
    latency_ms: int | None = Field(
        default=None,
        description="Observed latency for the successful smoke test, when applicable.",
    )
    output_preview: str | None = Field(
        default=None,
        description="Short preview of the smoke-test output, when available.",
    )
    error: str | None = Field(
        default=None,
        description="Error summary when the test failed.",
    )


class RuntimeTestCache(BaseModel):
    """Cached runtime test results included in the runtime status payload."""

    modal: RuntimeConnectivityTestResponse | None = Field(
        default=None,
        description="Most recent Modal connectivity test result, if one has been run.",
    )
    lm: RuntimeConnectivityTestResponse | None = Field(
        default=None,
        description="Most recent language-model connectivity test result, if one has been run.",
    )
    daytona: RuntimeConnectivityTestResponse | None = Field(
        default=None,
        description="Most recent Daytona connectivity test result, if one has been run.",
    )


class RuntimeActiveModels(BaseModel):
    """Resolved active model identifiers currently loaded by the runtime."""

    planner: str = Field(
        default="", description="Planner model identifier currently in use."
    )
    delegate: str = Field(
        default="", description="Delegate model identifier currently in use."
    )
    delegate_small: str = Field(
        default="",
        description="Small delegate model identifier currently in use, when configured.",
    )


class RuntimeStatusResponse(BaseModel):
    """Combined readiness and diagnostics snapshot for the runtime settings UI."""

    app_env: str = Field(
        description="Current application environment, such as `local` or `prod`."
    )
    write_enabled: bool = Field(
        description="Whether runtime settings writes are currently allowed."
    )
    ready: bool = Field(
        description="Whether critical runtime services are ready to serve requests."
    )
    active_models: RuntimeActiveModels = Field(
        description="Resolved planner and delegate model identities."
    )
    sandbox_provider: VolumeProvider = Field(
        default="modal",
        description="Active sandbox backend selected for runtime execution and volume browsing.",
    )
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description="Language-model configuration and readiness diagnostics.",
    )
    mlflow: dict[str, Any] = Field(
        default_factory=dict,
        description="MLflow enablement and startup diagnostics.",
    )
    modal: dict[str, Any] = Field(
        default_factory=dict,
        description="Modal configuration and readiness diagnostics.",
    )
    daytona: dict[str, Any] = Field(
        default_factory=dict,
        description="Daytona configuration and readiness diagnostics.",
    )
    tests: RuntimeTestCache = Field(
        description="Cached runtime connectivity test results exposed in the Settings UI."
    )
    guidance: list[str] = Field(
        default_factory=list,
        description="Human-readable remediation steps for incomplete runtime setup.",
    )


class VolumeTreeNode(BaseModel):
    """A single node in the volume file tree."""

    id: str = Field(
        description="Stable node identifier used by the frontend tree view."
    )
    name: str = Field(description="Display name for the file-system node.")
    path: str = Field(
        description="Absolute path for the file-system node within the runtime volume."
    )
    type: Literal["volume", "directory", "file"] = Field(
        description="Kind of file-system node represented by this entry."
    )
    children: list[VolumeTreeNode] = Field(
        default_factory=list,
        description="Child nodes for directory or volume entries.",
    )
    size: int | None = Field(
        default=None,
        description="File size in bytes when the provider reports one.",
    )
    modified_at: str | None = Field(
        default=None,
        description="Last modified timestamp when the provider reports one.",
    )


class VolumeTreeResponse(BaseModel):
    """Response for the volume tree listing endpoint."""

    provider: VolumeProvider = Field(
        description="Runtime volume backend used to satisfy the request."
    )
    volume_name: str = Field(
        description="Resolved volume name used for the listing request."
    )
    root_path: str = Field(
        description="Normalized root path used for the listing request."
    )
    nodes: list[VolumeTreeNode] = Field(
        description="Tree nodes rooted at the requested path."
    )
    total_files: int = Field(
        default=0,
        description="Total file count returned in the current response payload.",
    )
    total_dirs: int = Field(
        default=0,
        description="Total directory count returned in the current response payload.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the provider truncated the tree because of depth or payload limits.",
    )


class VolumeFileContentResponse(BaseModel):
    """Response for runtime volume file-content preview endpoint."""

    provider: VolumeProvider = Field(
        description="Runtime volume backend used to satisfy the request."
    )
    path: str = Field(description="Normalized file path used for the preview request.")
    mime: str = Field(description="Detected MIME type for the returned content.")
    size: int = Field(description="File size in bytes reported by the provider.")
    content: str = Field(
        description="UTF-8 text preview returned for the requested file."
    )
    truncated: bool = Field(
        default=False,
        description="Whether the returned file content was truncated to respect max_bytes.",
    )


class TraceFeedbackRequest(BaseModel):
    """Feedback payload for annotating an MLflow trace."""

    trace_id: str | None = Field(
        default=None,
        description="Resolved MLflow trace identifier when the client already knows it.",
    )
    client_request_id: str | None = Field(
        default=None,
        description="Client request identifier used to resolve the trace when trace_id is absent.",
    )
    is_correct: bool = Field(
        description="Whether the model output was considered correct."
    )
    comment: str | None = Field(
        default=None,
        description="Optional free-form reviewer comment explaining the feedback.",
    )
    expected_response: str | None = Field(
        default=None,
        description="Optional ground-truth response or correction to log alongside the feedback.",
    )

    @model_validator(mode="after")
    def validate_trace_lookup_target(self) -> TraceFeedbackRequest:
        if (self.trace_id or "").strip() or (self.client_request_id or "").strip():
            return self
        raise ValueError("trace_id or client_request_id is required")


class TraceFeedbackResponse(BaseModel):
    """Result payload after MLflow feedback has been recorded."""

    ok: bool = Field(
        default=True, description="Whether the feedback request completed successfully."
    )
    trace_id: str = Field(
        description="Resolved MLflow trace identifier that received the feedback."
    )
    client_request_id: str | None = Field(
        default=None,
        description="Resolved client request identifier associated with the trace, when available.",
    )
    feedback_logged: bool = Field(
        default=True,
        description="Whether binary/correctness feedback was successfully logged.",
    )
    expectation_logged: bool = Field(
        default=False,
        description="Whether an expected-response correction was successfully logged.",
    )
