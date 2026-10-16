"""Request and response models for every route."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_TASK_CHARS = 100_000


# Requests


class _Request(BaseModel):
    """Request bodies reject unknown fields, so a typo is a 422 rather than a silently ignored option."""

    model_config = ConfigDict(extra="forbid")


class TurnRequest(_Request):
    """User prompt sent to one Session's Fleet engine."""

    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ModeRequest(_Request):
    mode: str = Field(pattern="^(rlm|flex|hybrid)$")


class WorkspaceWriteRequest(_Request):
    content: str | None = Field(default=None, max_length=25_000_000)
    content_base64: str | None = Field(default=None, max_length=34_000_000)


class ContextRequest(_Request):
    path: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=2000)


class MemoryRequest(_Request):
    content: str = Field(min_length=1, max_length=10000)
    kind: str = Field(default="observation", pattern="^(observation|preference|hypothesis|procedure)$")
    sources: dict[str, str] = Field(default_factory=dict, max_length=100)
    reviewed: bool = False


class ModelRequest(_Request):
    role: str = Field(default="root", pattern="^(root|sub)$")
    model: str = Field(
        min_length=1, max_length=300, pattern=r"^openai/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+$"
    )


# Responses


class Health(BaseModel):
    status: str
    version: str


class SessionSummary(BaseModel):
    session_id: str
    updated_at: datetime
    turn_count: int


class Sessions(BaseModel):
    sessions: list[SessionSummary]


class Catalog(BaseModel):
    catalog_xml: str


class Exported(BaseModel):
    exported: list[str]


class Pushed(BaseModel):
    pushed: int


class Models(BaseModel):
    scope: str
    models: list[str]


class Limits(BaseModel):
    lm_calls: int
    depth: int
    children: int
    active_children: int
    timeout_seconds: int


class SessionConfig(BaseModel):
    mode: str
    models: dict[str, str]
    context: dict[str, Any] | None
    configuration: dict[str, Any]
    limits: Limits


class ModelSelection(BaseModel):
    role: str
    model: str


class Mode(BaseModel):
    mode: str


class History(BaseModel):
    session_id: str
    turns: list[dict[str, Any]]


class TurnStatus(BaseModel):
    turn_id: str
    status: str
    result: dict[str, Any]
    updated_at: str


class Memories(BaseModel):
    memories: list[dict[str, Any]]


class MemorySaved(BaseModel):
    memory_id: str


class Deleted(BaseModel):
    deleted: str


class Files(BaseModel):
    session_id: str
    files: list[str]
    artifacts: list[str]


class FileStat(BaseModel):
    path: str
    size_bytes: int
    sha256: str


class FileWritten(FileStat):
    mounted_path: str


class ContextSelected(BaseModel):
    path: str
    size_bytes: int
    sha256: str


class Status(BaseModel):
    status: str


class Attached(BaseModel):
    mounted_path: str
    rel_path: str
