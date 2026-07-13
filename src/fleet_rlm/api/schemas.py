"""HTTP schemas for the Fleet RLM public API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Body for POST /api/chat."""

    message: str = Field(..., min_length=1, max_length=100_000)
    session_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)


class AttachmentResponse(BaseModel):
    """Public attachment metadata — no host or Volume paths."""

    id: UUID
    filename: str
    content_type: str | None = None
    byte_size: int
    checksum_sha256: str


class ArtifactResponse(BaseModel):
    """Public artifact metadata — no host or Volume paths."""

    id: UUID
    session_id: UUID
    run_id: UUID
    kind: str
    title: str | None = None
    media_type: str
    byte_size: int
    checksum_sha256: str


class SkillCardResponse(BaseModel):
    """Bounded Skill discovery metadata — no instructions body."""

    id: UUID
    name: str
    description: str
    scope: str
    version: str
    trust: str
    affordances: list[str]
    resources_available: bool


# ---------------------------------------------------------------------------
# Sessions (durable conversation CRUD)
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class SessionPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, description="active | archived")


class SessionSummaryResponse(BaseModel):
    id: UUID
    title: str
    status: str
    checkpoint_version: int
    created_at: str | None = None
    updated_at: str | None = None


class SessionDetailResponse(BaseModel):
    id: UUID
    title: str
    status: str
    checkpoint_version: int
    turn_count: int
    created_at: str | None = None
    updated_at: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionSummaryResponse]
    total: int
    offset: int
    limit: int
    has_more: bool


class TurnResponse(BaseModel):
    id: UUID
    sequence: int
    role: str
    content: str
    status: str
    run_id: UUID | None = None
    parts: list[dict[str, Any]]
    metadata: dict[str, Any] | None = None


class TurnListResponse(BaseModel):
    items: list[TurnResponse]
    total: int
    offset: int
    limit: int
    has_more: bool
