"""Attachment domain models (re-exported from service.py)."""

from fleet_rlm.attachments.service import (
    AsyncByteSource,
    AttachmentAccess,
    AttachmentRef,
    AttachmentRun,
    AttachmentUpload,
    PreparedAttachment,
    PreparedAttachments,
    RunAttachmentSink,
    StagedAttachment,
)

__all__ = [
    "AsyncByteSource",
    "AttachmentAccess",
    "AttachmentRef",
    "AttachmentRun",
    "AttachmentUpload",
    "PreparedAttachment",
    "PreparedAttachments",
    "RunAttachmentSink",
    "StagedAttachment",
]
