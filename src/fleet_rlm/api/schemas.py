"""Request and response models for every route."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fleet_rlm.config import validate_turn_timeout_seconds

MAX_TASK_CHARS = 100_000


# Requests


class _Request(BaseModel):
    """Request bodies reject unknown fields, so a typo is a 422 rather than a silently ignored option."""

    model_config = ConfigDict(extra="forbid")


class TurnRequest(_Request):
    """User prompt sent to one Session's Fleet engine."""

    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    idempotency_key: str | None = Field(default=None, max_length=128)
    record_replay: bool = Field(
        default=False,
        description="Persist a bounded immutable snapshot of this Turn's inputs for explicitly reviewed GEPA data.",
    )
    timeout_seconds: int | None = Field(
        default=None,
        strict=True,
        description=(
            "Fleet deadline for this Turn in seconds. Omitted or null inherits the configured "
            "value; 0 disables the Fleet deadline; otherwise the value must be 10..3600."
        ),
    )

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def timeout_is_zero_or_bounded(cls, value: Any) -> int | None:
        """Preserve None for inheritance; accept integer 0 or 10..3600 seconds.

        Raise ValueError for other values, including booleans, floats, and strings.
        """
        if value is None:
            return None
        return validate_turn_timeout_seconds(value, field="timeout_seconds")


class ModeRequest(_Request):
    """Request to switch a Session's execution mode to rlm, flex, or hybrid."""

    mode: str = Field(pattern="^(rlm|flex|hybrid)$")


class WorkspaceWriteRequest(_Request):
    """File content for a workspace write, as text or base64; supply exactly one."""

    content: str | None = Field(default=None, max_length=25_000_000)
    content_base64: str | None = Field(default=None, max_length=34_000_000)


class ContextRequest(_Request):
    """Workspace file to select as the Session's context."""

    path: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=2000)


class MemoryRequest(_Request):
    """Memory entry to save, with its kind, sources, and review state."""

    content: str = Field(min_length=1, max_length=10000)
    kind: str = Field(default="observation", pattern="^(observation|preference|hypothesis|procedure)$")
    sources: dict[str, str] = Field(default_factory=dict, max_length=100)
    reviewed: bool = False


class ModelRequest(_Request):
    """Model to bind to the root or sub role of a Session's engine."""

    role: str = Field(default="root", pattern="^(root|sub)$")
    model: str = Field(
        min_length=1, max_length=300, pattern=r"^openai/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+$"
    )


# Responses


class MlflowTraceLocation(BaseModel):
    """Unity Catalog location associated with the runtime experiment."""

    catalog: str
    schema_: str = Field(alias="schema")
    table_prefix: str


class MlflowHealth(BaseModel):
    """Non-secret, initialized tracking destination for process reuse checks."""

    mode: Literal["local", "databricks"]
    tracking_uri: str
    workspace_host: str | None = None
    experiment_name: str
    eval_experiment_name: str
    optimization_experiment_name: str
    trace_location: MlflowTraceLocation | None = None
    experiment_id: str | None = None
    ready: bool


class Health(BaseModel):
    """Service status, version, and effective MLflow destination."""

    status: str
    version: str
    mlflow: MlflowHealth


class SessionSummary(BaseModel):
    """One stored Session with its last update time and Turn count."""

    session_id: str
    updated_at: datetime
    turn_count: int


class Sessions(BaseModel):
    """Listing of every stored Session."""

    sessions: list[SessionSummary]


class Catalog(BaseModel):
    """Session Skill catalog rendered as XML."""

    catalog_xml: str


class Exported(BaseModel):
    """Skills exported from the Session's volume to local storage."""

    exported: list[str]


class Pushed(BaseModel):
    """Count of local Skills pushed to the Session's volume."""

    pushed: int


class Models(BaseModel):
    """Chat models available in one discovery scope."""

    scope: str
    models: list[str]


class Limits(BaseModel):
    """Execution limits the sandbox manager enforces for one Session."""

    lm_calls: int
    depth: int
    children: int
    active_children: int
    timeout_seconds: int


class SessionConfig(BaseModel):
    """Effective mode, models, context, configuration, and limits of one Session."""

    mode: str
    models: dict[str, str]
    context: dict[str, Any] | None
    configuration: dict[str, Any]
    limits: Limits


class ModelSelection(BaseModel):
    """Model bound to one role after a successful selection."""

    role: str
    model: str


class Mode(BaseModel):
    """Execution mode of one Session."""

    mode: str


class History(BaseModel):
    """Stored Turn records for one Session."""

    session_id: str
    turns: list[dict[str, Any]]


class TurnStatus(BaseModel):
    """Recorded status and result of one Turn."""

    turn_id: str
    status: str
    result: dict[str, Any]
    updated_at: str


class Memories(BaseModel):
    """Memory entries matching a Session memory query."""

    memories: list[dict[str, Any]]


class MemorySaved(BaseModel):
    """Acknowledgement of a saved memory entry."""

    memory_id: str


class Deleted(BaseModel):
    """Acknowledgement of a deleted memory entry or workspace file."""

    deleted: str


class Files(BaseModel):
    """Workspace file paths for one Session, with the artifacts among them."""

    session_id: str
    files: list[str]
    artifacts: list[str]


class FileStat(BaseModel):
    """Size and SHA-256 digest of one workspace file."""

    path: str
    size_bytes: int
    sha256: str


class FileWritten(FileStat):
    """Result of a workspace write, including the sandbox mounted path."""

    mounted_path: str


class ContextSelected(BaseModel):
    """Workspace file recorded as the Session's context, with its size and digest."""

    path: str
    size_bytes: int
    sha256: str


class Status(BaseModel):
    """Plain status acknowledgement, such as a cleared context."""

    status: str


class Attached(BaseModel):
    """Location of an uploaded attachment in the workspace and the sandbox."""

    mounted_path: str
    rel_path: str
