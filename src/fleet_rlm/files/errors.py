"""Attachment / upload errors."""

from __future__ import annotations


class AttachmentError(RuntimeError):
    """Base attachment error."""


class AttachmentNotFoundError(AttachmentError):
    """Missing or unauthorized attachment (do not distinguish for clients)."""


class AttachmentValidationError(AttachmentError):
    """Rejected filename, size, or content."""


class AttachmentIntegrityError(AttachmentError):
    """Authorized durable bytes are absent or contradict their metadata."""


class AttachmentStorageError(AttachmentError):
    """A required catalog, durable blob, or Run sink is unavailable."""
