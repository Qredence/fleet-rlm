"""Pydantic schemas for runtime artifact references."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactCategory = Literal["plans", "reports", "data"]


class ArtifactRef(BaseModel):
    """Stable reference to one runtime artifact."""

    id: str
    session_id: str
    category: ArtifactCategory | str
    path: str
    uri: str
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None


class ArtifactMetadata(BaseModel):
    """Metadata captured when an artifact is created or registered."""

    ref: ArtifactRef
    created_at: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["ArtifactCategory", "ArtifactMetadata", "ArtifactRef"]
