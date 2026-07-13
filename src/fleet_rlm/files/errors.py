"""Attachment / upload errors."""

from __future__ import annotations


class AttachmentError(RuntimeError):
    """Base attachment error."""


class AttachmentNotFoundError(AttachmentError):
    """Missing or unauthorized attachment (do not distinguish for clients)."""


class AttachmentValidationError(AttachmentError):
    """Rejected filename, size, or content."""
