"""Attachment lifecycle service and protocols (re-exported from service.py)."""

from fleet_rlm.attachments.service import (
    AttachmentBlobGateway,
    AttachmentCatalog,
    AttachmentLifecycle,
    AttachmentLifecycleService,
    AttachmentPathPolicy,
    StoredAttachment,
)

__all__ = [
    "AttachmentBlobGateway",
    "AttachmentCatalog",
    "AttachmentLifecycle",
    "AttachmentLifecycleService",
    "AttachmentPathPolicy",
    "StoredAttachment",
]
