"""Pydantic request/response schemas for the /api/chat SSE endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .runtime import ExecutionMode


class ChatMessage(BaseModel):
    """A single message in the AI SDK UIMessage format."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "system", "tool"] = Field(
        description="Message role in the conversation."
    )
    content: str | None = Field(
        default=None,
        description="Message text content. May be None when parts are provided.",
    )
    parts: list[dict[str, Any]] | None = Field(
        default=None,
        description="AI SDK UIMessage parts for structured content.",
    )


class ChatRequest(BaseModel):
    """Request body for the /api/chat SSE endpoint."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(
        min_length=1,
        description="Conversation messages. Must have at least one message.",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session identifier for restoring an existing session.",
    )
    execution_mode: ExecutionMode | None = Field(
        default=None,
        description="Per-turn execution mode hint. Accepts legacy values (auto/rlm_only/tools_only).",
    )
    repo_url: str | None = Field(
        default=None,
        description="Repository URL to attach to runs.",
    )
    repo_ref: str | None = Field(
        default=None,
        description="Optional branch, tag, or commit to checkout.",
    )
    context_paths: list[str] | None = Field(
        default=None,
        description="Optional repository paths to prioritize as context.",
    )
    batch_concurrency: int | None = Field(
        default=None,
        description="Optional concurrency hint for batched repository work.",
    )
    docs_path: str | None = Field(
        default=None,
        description="Optional local documentation path to preload before execution.",
    )
    trace: bool | None = Field(
        default=None,
        description="Whether trace-oriented streaming events should be emitted.",
    )
    trace_mode: str | None = Field(
        default=None,
        description="Optional trace verbosity override.",
    )
    selected_skill_ids: list[str] | None = Field(
        default=None,
        description="Optional list of skill IDs to select for this turn.",
    )
