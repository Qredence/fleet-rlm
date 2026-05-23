"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any

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
