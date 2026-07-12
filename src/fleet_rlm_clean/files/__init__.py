"""Attachment upload and staging for the clean backend."""

from __future__ import annotations

from fleet_rlm_clean.files.errors import (
    AttachmentError,
    AttachmentNotFoundError,
    AttachmentValidationError,
)
from fleet_rlm_clean.files.models import AttachmentRef, StagedAttachment
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.uploads import LocalAttachmentStore

__all__ = [
    "AttachmentError",
    "AttachmentNotFoundError",
    "AttachmentRef",
    "AttachmentStager",
    "AttachmentValidationError",
    "LocalAttachmentStore",
    "StagedAttachment",
]
