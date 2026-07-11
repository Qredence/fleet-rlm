"""HTTP schemas for the clean-backend public API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Body for POST /api/chat."""

    message: str = Field(..., min_length=1, max_length=100_000)
    session_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list)
