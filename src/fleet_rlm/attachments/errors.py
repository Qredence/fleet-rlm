"""Attachment / upload errors (re-exported from service.py)."""

from fleet_rlm.attachments.service import (
    AttachmentError,
    AttachmentIntegrityError,
    AttachmentNotFoundError,
    AttachmentStorageError,
    AttachmentValidationError,
)

__all__ = [
    "AttachmentError",
    "AttachmentIntegrityError",
    "AttachmentNotFoundError",
    "AttachmentStorageError",
    "AttachmentValidationError",
]
