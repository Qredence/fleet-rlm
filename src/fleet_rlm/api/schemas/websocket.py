"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from fleet_rlm.runtime.schemas import TraceMode

from .runtime import ExecutionMode


class WSMessage(BaseModel):
    """Typed websocket payload for chat, cancel, and command frames."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["message", "cancel", "command"] = Field(
        description="Websocket frame type.",
    )
    content: str = Field(default="", description="Primary chat content for message frames.")
    docs_path: str | None = Field(
        default=None,
        description="Optional local documentation path to preload before execution.",
    )
    trace: bool = Field(
        default=True,
        description="Whether trace-oriented streaming events should be emitted for the turn.",
    )
    trace_mode: TraceMode | None = Field(
        default=None,
        description="Optional trace verbosity override for the websocket session.",
    )
    execution_mode: ExecutionMode = Field(
        default="auto",
        description="Per-turn execution mode hint for the Daytona-backed websocket runtime.",
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
    session_id: str | None = Field(
        default=None,
        description="Optional session identifier for restoring an existing websocket session.",
    )
    # Command dispatch fields (used when type == "command")
    command: str = Field(default="", description="Command name when `type` is `command`.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Command arguments when `type` is `command`.",
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_daytona_message_contract(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw

        if "type" not in raw:
            raise PydanticCustomError(
                "websocket_type_required",
                "WebSocket frames must include an explicit canonical type.",
            )

        if "workspace_id" in raw or "user_id" in raw:
            raise PydanticCustomError(
                "unsupported_identity_fields",
                "WebSocket identity is derived from auth. Remove workspace_id/user_id and use session_id only.",
            )

        message_type = str(raw.get("type", "message") or "message").strip()
        if message_type == "message" and not str(raw.get("content", "") or "").strip():
            raise PydanticCustomError(
                "websocket_message_content_required",
                "Canonical websocket message frames require non-empty content.",
            )
        if message_type == "message" and raw.get("max_depth") is not None:
            raise PydanticCustomError(
                "daytona_max_depth_removed",
                "Daytona websocket requests no longer accept max_depth; use the server-configured recursion depth.",
            )

        if (
            message_type == "message"
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
    session_id: str | None = None


class WSCommandResult(BaseModel):
    """Server response for a command dispatch."""

    type: Literal["command_result"] = "command_result"
    command: str
    result: dict[str, Any] = Field(default_factory=dict)
