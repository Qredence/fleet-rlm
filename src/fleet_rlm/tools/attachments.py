"""Tool-facing attachment schema exports."""

from __future__ import annotations

from fleet_rlm.files.schemas import AttachedFiles, AttachmentRef
from fleet_rlm.files.staging import AttachmentStagingRequest, AttachmentStagingResult, AttachmentStagingTarget

__all__ = [
    "AttachedFiles",
    "AttachmentRef",
    "AttachmentStagingRequest",
    "AttachmentStagingResult",
    "AttachmentStagingTarget",
]
