"""Attachment and file-staging primitives."""

from __future__ import annotations

from .schemas import AttachedFiles, AttachmentRef
from .staging import AttachmentStagingRequest, AttachmentStagingResult, AttachmentStagingTarget

__all__ = [
    "AttachedFiles",
    "AttachmentRef",
    "AttachmentStagingRequest",
    "AttachmentStagingResult",
    "AttachmentStagingTarget",
]
