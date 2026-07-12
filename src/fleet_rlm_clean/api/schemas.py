"""HTTP schemas for the clean-backend public API."""

from __future__ import annotations

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


class StageAttachmentRequest(BaseModel):
    session_id: UUID
    run_id: UUID


class StagedAttachmentResponse(BaseModel):
    """Staging result exposes only a Fleet-controlled Sandbox path."""

    attachment_id: UUID
    sandbox_path: str


class CreateArtifactRequest(BaseModel):
    """Create a durable text/markdown/json artifact for a session run."""

    session_id: UUID
    run_id: UUID
    kind: str = Field(..., description="text | markdown | json")
    content: str = Field(..., min_length=1)
    title: str | None = None


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

