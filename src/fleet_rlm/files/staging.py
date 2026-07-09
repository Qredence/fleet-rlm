"""Interfaces for future Daytona attachment staging."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from .schemas import AttachmentRef


class AttachmentStagingRequest(BaseModel):
    """Request shape for staging an uploaded file later."""

    attachment: AttachmentRef
    session_id: str
    content: bytes = Field(repr=False)


class AttachmentStagingResult(BaseModel):
    """Result shape for a staged attachment."""

    attachment: AttachmentRef
    sandbox_path: str


class AttachmentStagingTarget(Protocol):
    """Protocol implemented by future Daytona attachment stagers."""

    def stage_attachment(self, request: AttachmentStagingRequest) -> AttachmentStagingResult:
        """Stage an attachment into a sandbox/volume path."""


__all__ = ["AttachmentStagingRequest", "AttachmentStagingResult", "AttachmentStagingTarget"]
