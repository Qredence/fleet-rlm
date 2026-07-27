"""Attachment upload and staging for the clean backend."""

from __future__ import annotations

from fleet_rlm.files.errors import (
    AttachmentError,
    AttachmentNotFoundError,
    AttachmentValidationError,
)
from fleet_rlm.files.lifecycle import AttachmentLifecycleService, StoredAttachment
from fleet_rlm.files.models import AttachmentRef, StagedAttachment
from fleet_rlm.files.tools import FileToolHost

__all__ = [
    "AttachmentError",
    "AttachmentLifecycleService",
    "AttachmentNotFoundError",
    "AttachmentRef",
    "AttachmentValidationError",
    "FileToolHost",
    "StagedAttachment",
    "StoredAttachment",
]
