"""API request/response models for file upload endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fleet_rlm.files.schemas import AttachmentRef


class UploadedFileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    content_type: str | None = None
    size_bytes: int = Field(ge=0)
    checksum_sha256: str | None = None
    created_at: datetime | None = None


class FileUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment: AttachmentRef
    uploaded: UploadedFileMetadata


__all__ = [
    "FileUploadResponse",
    "UploadedFileMetadata",
]
