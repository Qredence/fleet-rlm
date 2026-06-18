"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


# ---------------------------------------------------------------------------
# Session history (durable transcript store)
# ---------------------------------------------------------------------------


class SessionListItem(BaseModel):
    """Lightweight session summary for list views."""

    id: str = Field(description="Durable session identifier.")
    title: str = Field(description="Human-readable session title.")
    status: str = Field(description="Session status (active, archived).")
    model_name: str | None = Field(default=None, description="Model used in session.")
    external_session_id: str | None = Field(default=None, description="Canonical runtime session identifier.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    updated_at: str = Field(description="ISO-8601 last-update timestamp.")


class SessionListResponse(BaseModel):
    """Paginated session list."""

    items: list[SessionListItem] = Field(description="Session list items.")
    total: int = Field(description="Total matching sessions.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
    has_more: bool = Field(description="Whether more results exist beyond this page.")


class SessionDetailResponse(BaseModel):
    """Full session detail with turn count."""

    id: str = Field(description="Durable session identifier.")
    title: str = Field(description="Human-readable session title.")
    status: str = Field(description="Session status (active, archived).")
    model_name: str | None = Field(default=None, description="Model used in session.")
    external_session_id: str | None = Field(default=None, description="Canonical runtime session identifier.")
    workspace_id: str | None = Field(default=None, description="Workspace context.")
    turn_count: int = Field(description="Total number of turns in this session.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")
    updated_at: str = Field(description="ISO-8601 last-update timestamp.")


class TurnItem(BaseModel):
    """Single turn in a session transcript."""

    id: str = Field(description="Durable turn identifier.")
    turn_index: int = Field(description="Zero-based turn position.")
    user_message: str = Field(description="User message text.")
    assistant_message: str | None = Field(default=None, description="Assistant response text.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")


class TurnListResponse(BaseModel):
    """Paginated turn list."""

    items: list[TurnItem] = Field(description="Turn list items.")
    total: int = Field(description="Total turns in session.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
    has_more: bool = Field(description="Whether more turns exist beyond this page.")


class SessionDeleteResponse(BaseModel):
    """Result payload after archiving a session."""

    ok: bool = Field(
        default=True,
        description="Whether the session was archived successfully.",
    )


class SessionRestoreResponse(BaseModel):
    """Result payload after restoring an archived session."""

    ok: bool = Field(
        default=True,
        description="Whether the session was restored successfully.",
    )


class SessionPatchRequest(BaseModel):
    """Patch body for updating session metadata."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        description="New human-readable session title.",
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        description="New metadata dictionary to merge or replace session metadata.",
    )


class SessionTraceItem(BaseModel):
    """External trace metadata linked to a durable session."""

    trace_id: str = Field(description="Provider trace identifier (for example an MLflow trace id).")
    client_request_id: str | None = Field(
        default=None,
        description="Optional Fleet client request id correlated with the trace.",
    )
    turn_id: str | None = Field(default=None, description="Chat turn id when the trace was recorded.")
    provider: str = Field(description="External trace provider (for example mlflow).")
    experiment_id: str | None = Field(default=None, description="Provider experiment id when known.")
    experiment_name: str | None = Field(default=None, description="Provider experiment name when known.")
    observed_at: str = Field(description="ISO-8601 timestamp when the trace was observed.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific metadata payload stored with the trace row.",
    )


class SessionTraceListResponse(BaseModel):
    """Paginated external traces for a session."""

    items: list[SessionTraceItem] = Field(description="Trace rows linked to the session.")
    total: int = Field(description="Total matching traces.")
    offset: int = Field(description="Pagination offset.")
    limit: int = Field(description="Page size.")
    has_more: bool = Field(description="Whether additional pages are available.")


class SessionTraceDebugSpan(BaseModel):
    """One MLflow span classified against the chat transcript component model."""

    span_id: str = Field(description="MLflow/OpenTelemetry span identifier.")
    parent_span_id: str | None = Field(default=None, description="Parent span identifier when present.")
    name: str = Field(description="Span name.")
    span_type: str | None = Field(default=None, description="MLflow span type when present.")
    status_code: str | None = Field(default=None, description="Span status code.")
    tool_name: str | None = Field(default=None, description="Resolved tool name when the span is tool-like.")
    mapped_render_kind: Literal[
        "assistant_text",
        "reasoning",
        "tool",
        "sandbox",
        "status_note",
        "non_rendered",
    ] = Field(description="Frontend chat render kind the span most closely maps to.")
    mapped_component_type: str | None = Field(
        default=None,
        description="Frontend Agent Elements component/tool type hint when renderable.",
    )
    rationale: str = Field(description="Why the span is rendered or intentionally not rendered.")
    input_preview: str | None = Field(default=None, description="Compact preview of span inputs.")
    output_preview: str | None = Field(default=None, description="Compact preview of span outputs.")
    start_time_unix_nano: str | None = Field(
        default=None,
        description="Span start timestamp (Unix nanoseconds, string-encoded).",
    )
    end_time_unix_nano: str | None = Field(
        default=None,
        description="Span end timestamp (Unix nanoseconds, string-encoded).",
    )
    duration_ms: int | None = Field(default=None, description="Span duration in milliseconds when timestamps exist.")
    input_tokens: int | None = Field(default=None, description="Input token count reported for this span.")
    output_tokens: int | None = Field(default=None, description="Output token count reported for this span.")
    total_tokens: int | None = Field(default=None, description="Total token count reported for this span.")
    output_chars: int | None = Field(default=None, description="Character count of the raw span output payload.")
    retry_or_fallback_reason: str | None = Field(
        default=None,
        description="Parse, retry, or adapter fallback signal detected for this span.",
    )


class SessionTracePerformanceSpanSummary(BaseModel):
    """Compact span reference used in trace performance summaries."""

    span_id: str = Field(description="Span identifier.")
    name: str = Field(description="Span name.")
    duration_ms: int | None = Field(default=None, description="Span duration in milliseconds.")
    input_tokens: int | None = Field(default=None, description="Input token count.")
    output_tokens: int | None = Field(default=None, description="Output token count.")
    total_tokens: int | None = Field(default=None, description="Total token count.")
    output_chars: int | None = Field(default=None, description="Output payload character count.")


class SessionTracePerformanceSummary(BaseModel):
    """Performance and token summary derived from raw MLflow trace spans."""

    total_duration_ms: int | None = Field(default=None, description="Root trace duration in milliseconds.")
    llm_duration_ms: int = Field(default=0, description="Total duration of LLM/chat-model spans.")
    repl_duration_ms: int = Field(default=0, description="Total duration of REPL execution spans.")
    tool_duration_ms: int = Field(default=0, description="Total duration of non-REPL tool spans.")
    root_overhead_ms: int | None = Field(
        default=None,
        description="Root duration minus known LLM, REPL, and tool durations.",
    )
    input_tokens: int = Field(default=0, description="Summed input tokens from span usage.")
    output_tokens: int = Field(default=0, description="Summed output tokens from span usage.")
    total_tokens: int = Field(default=0, description="Summed total tokens from span usage.")
    token_total_mismatch: bool = Field(
        default=False,
        description="Whether total_tokens differs from input_tokens + output_tokens.",
    )
    adapter_fallback_count: int = Field(default=0, description="Detected adapter fallback or retry signals.")
    parse_error_count: int = Field(default=0, description="Detected parser/adapter parse error signals.")
    selected_skills: list[str] = Field(default_factory=list, description="Selected RLM skill names.")
    rlm_action_max_tokens: int | None = Field(
        default=None, description="Configured RLM action-generation token budget."
    )
    rlm_max_output_chars: int | None = Field(default=None, description="Configured RLM REPL output character budget.")
    slowest_llm_span: SessionTracePerformanceSpanSummary | None = Field(
        default=None,
        description="Slowest detected LLM/chat-model span.",
    )
    largest_output_span: SessionTracePerformanceSpanSummary | None = Field(
        default=None,
        description="Span with the largest output payload.",
    )


class SessionTraceDebugResponse(BaseModel):
    """Session-scoped MLflow trace debug summary for chat component mapping."""

    trace_id: str = Field(description="Resolved MLflow trace identifier.")
    client_request_id: str | None = Field(
        default=None,
        description="Resolved Fleet client request identifier when available.",
    )
    state: str | None = Field(default=None, description="Top-level MLflow trace state.")
    request_preview: str | None = Field(default=None, description="Trace request preview.")
    response_preview: str | None = Field(default=None, description="Trace response preview.")
    resolved_from: Literal["trace_id", "client_request_id", "session_row", "runtime_session_id"] = Field(
        description="How the trace was resolved for this session debug request."
    )
    runtime_session_id: str | None = Field(
        default=None,
        description="Authorized runtime session id used for fallback lookup when applicable.",
    )
    span_count: int = Field(description="Total spans in the resolved trace.")
    renderable_span_count: int = Field(description="How many spans map to a renderable chat component.")
    non_rendered_span_count: int = Field(description="How many spans are intentionally observability-only.")
    performance_summary: SessionTracePerformanceSummary = Field(
        description="Performance, token, and fallback summary derived from raw spans."
    )
    spans: list[SessionTraceDebugSpan] = Field(description="Per-span mapping summary.")


class SessionStatsResponse(BaseModel):
    """Aggregated usage stats for a session."""

    total_tokens_in: int = Field(
        default=0,
        description="Total input tokens across all turns.",
    )
    total_tokens_out: int = Field(
        default=0,
        description="Total output tokens across all turns.",
    )
    total_latency_ms: int = Field(
        default=0,
        description="Total latency in milliseconds across all turns.",
    )
    model_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Mapping of model_name to turn count.",
    )


class SessionExportRequest(BaseModel):
    """Request body for exporting a session's turns as a GEPA training dataset."""

    model_config = ConfigDict(extra="forbid")

    module_slug: str = Field(
        description="Target GEPA module slug whose dataset keys determine the export column mapping."
    )


class SessionTraceExportRequest(BaseModel):
    """Request body for exporting a session's linked MLflow traces."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "jsonl", "both"] = Field(
        default="both",
        description="Trace artifact format to write.",
    )
    mlflow_session_id: str | None = Field(
        default=None,
        description=(
            "Optional MLflow trace session id hint. The server validates the hint against "
            "authorized runtime session ids for the resolved durable session before export."
        ),
    )


class SessionTraceExportResponse(BaseModel):
    """Trace export artifact paths for a session."""

    ok: bool = Field(default=True, description="Whether trace export completed.")
    session_id: str = Field(description="Durable session identifier.")
    trace_count: int = Field(description="Number of MLflow traces exported.")
    json_path: str | None = Field(default=None, description="Path to the full JSON trace artifact.")
    jsonl_path: str | None = Field(default=None, description="Path to the full JSONL trace artifact.")
    distilled_bundle_path: str | None = Field(
        default=None,
        description="Path to the distilled GEPA evidence bundle.",
    )
    skipped_trace_ids: list[str] = Field(
        default_factory=list,
        description="Trace identifiers that could not be resolved/exported.",
    )
    errors: list[str] = Field(default_factory=list, description="Non-fatal export errors.")
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Distilled trace export summary.",
    )


class TranscriptTurnInput(BaseModel):
    """Single transcript turn used to build a GEPA dataset."""

    user_message: str | None = Field(
        default=None,
        description="User prompt/content for the turn.",
    )
    assistant_message: str | None = Field(
        default=None,
        description="Assistant response/content for the turn.",
    )


class TranscriptDatasetRequest(BaseModel):
    """Request body for converting transcript turns into a GEPA dataset."""

    module_slug: str = Field(description="Target GEPA module slug whose dataset keys determine row mapping.")
    title: str | None = Field(
        default=None,
        description="Optional human-readable transcript title used for dataset naming.",
    )
    turns: list[TranscriptTurnInput] = Field(
        description="Transcript turns to convert into dataset rows.",
    )
