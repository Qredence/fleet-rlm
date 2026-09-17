"""Local Attachment catalog & blob gateway (re-exported from service.py)."""

from fleet_rlm.attachments.service import (
    LocalAttachmentBlobGateway,
    LocalAttachmentCatalog,
)

__all__ = [
    "LocalAttachmentBlobGateway",
    "LocalAttachmentCatalog",
]
