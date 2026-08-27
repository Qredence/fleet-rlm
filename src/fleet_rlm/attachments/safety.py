"""Upload validation: names, size, no path traversal."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from fleet_rlm.attachments.errors import AttachmentValidationError

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,255}$")
DEFAULT_MAX_BYTES = 10 * 1024 * 1024


def sanitize_filename(filename: str) -> str:
    raw = (filename or "").strip()
    if not raw:
        raise AttachmentValidationError("invalid filename")
    # Reject any path-shaped input before taking the basename.
    if "/" in raw or "\\" in raw or ".." in raw:
        raise AttachmentValidationError("invalid filename")
    name = PurePosixPath(raw).name
    if not name or name in {".", ".."} or not _SAFE_NAME.match(name):
        raise AttachmentValidationError("invalid filename")
    if name.startswith("."):
        raise AttachmentValidationError("hidden filenames are not allowed")
    return name


def validate_upload_size(size: int, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    if size < 0:
        raise AttachmentValidationError("negative size")
    if size > max_bytes:
        raise AttachmentValidationError(f"file exceeds max size of {max_bytes} bytes")
    if size == 0:
        raise AttachmentValidationError("empty file")
