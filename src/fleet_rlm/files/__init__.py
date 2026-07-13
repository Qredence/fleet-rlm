"""Attachment upload and staging for the clean backend."""

from __future__ import annotations

from fleet_rlm.files.errors import (
    AttachmentError,
    AttachmentNotFoundError,
    AttachmentValidationError,
)
from fleet_rlm.files.models import AttachmentRef, StagedAttachment
from fleet_rlm.files.staging import AttachmentStager
from fleet_rlm.files.tools import FileToolHost
from fleet_rlm.files.uploads import LocalAttachmentStore

__all__ = [
    "AttachmentError",
    "AttachmentNotFoundError",
    "AttachmentRef",
    "AttachmentStager",
    "AttachmentValidationError",
    "FileToolHost",
    "LocalAttachmentStore",
    "StagedAttachment",
]
