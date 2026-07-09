"""Pydantic schemas for runtime artifact references."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from fleet_rlm.tools.paths import (
    PathSafetyError,
    reject_backslash_paths,
    reject_encoded_traversal_tokens,
    reject_host_drive_paths,
)

ArtifactCategory = Literal["plans", "reports", "data"]


class ArtifactRef(BaseModel):
    """Stable reference to one runtime artifact."""

    id: str
    session_id: str
    category: ArtifactCategory | str
    path: str = Field(description="Safe relative artifact path under the approved volume root.")
    uri: str
    mime_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None

    @field_validator("path")
    @classmethod
    def _path_must_be_safe_relative(cls, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("path must not be empty")
        try:
            reject_encoded_traversal_tokens(raw)
            reject_backslash_paths(raw)
            reject_host_drive_paths(raw)
        except PathSafetyError as exc:
            raise ValueError(str(exc)) from exc
        if raw.startswith("/") or ".." in raw.split("/"):
            raise ValueError("path must be a safe relative artifact path")
        return raw


class ArtifactMetadata(BaseModel):
    """Metadata captured when an artifact is created or registered."""

    ref: ArtifactRef
    created_at: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["ArtifactCategory", "ArtifactMetadata", "ArtifactRef"]
