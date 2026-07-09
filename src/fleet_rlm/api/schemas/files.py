"""API request/response models for file upload endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fleet_rlm.files.schemas import AttachmentRef


class UploadedFileMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, description="Uploaded file display name.")
    content_type: str | None = Field(default=None, description="Uploaded file MIME type when provided.")
    size_bytes: int = Field(ge=0, description="Uploaded file size in bytes.")
    checksum_sha256: str | None = Field(default=None, description="SHA-256 checksum of uploaded bytes.")
    created_at: datetime | None = Field(default=None, description="Server timestamp when the upload was staged.")


class FileUploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment: AttachmentRef
    uploaded: UploadedFileMetadata


__all__ = [
    "FileUploadResponse",
    "UploadedFileMetadata",
]
