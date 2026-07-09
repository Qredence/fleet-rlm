"""Pydantic schemas for staged chat attachments."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class AttachmentRef(BaseModel):
    """Reference to one uploaded or staged user file."""

    id: str = Field(min_length=1, description="Stable attachment identifier.")
    filename: str = Field(min_length=1, description="Display filename (basename only).")
    mime_type: str | None = Field(default=None, description="Best-effort MIME type for the attachment.")
    size_bytes: int = Field(ge=0, description="Attachment size in bytes.")
    checksum: str | None = Field(default=None, description="Optional content checksum when available.")
    staging_path: str | None = Field(
        default=None,
        description="Safe relative staging path under the approved uploads root.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional attachment metadata bag.")

    @field_validator("filename")
    @classmethod
    def _filename_must_be_simple(cls, value: str) -> str:
        raw = value.strip()
        if not raw or "/" in raw or "\\" in raw or ".." in raw:
            raise ValueError("filename must be a simple basename")
        return raw


class AttachedFiles(BaseModel):
    """Collection of attachment references for a future chat turn."""

    attachments: list[AttachmentRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _attachment_ids_must_be_unique(self) -> "AttachedFiles":
        ids = [attachment.id for attachment in self.attachments]
        if len(ids) != len(set(ids)):
            raise ValueError("attachment ids must be unique")
        return self


__all__ = ["AttachedFiles", "AttachmentRef"]
