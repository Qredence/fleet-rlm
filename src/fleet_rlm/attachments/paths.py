"""Attachment path policies (re-exported from service.py)."""

from fleet_rlm.attachments.service import (
    LocalAttachmentPathPolicy,
    WorkspaceAttachmentPathPolicy,
)

__all__ = [
    "LocalAttachmentPathPolicy",
    "WorkspaceAttachmentPathPolicy",
]
