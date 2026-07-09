"""Durable attachment upload staging under approved volume roots.

This module implements Phase 5's backend-only upload+staging slice.
It stages uploaded bytes into the Daytona persistent volume layout under
the existing canonical `/uploads` root, without parsing or executing content.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fleet_rlm.tools.paths import (
    reject_backslash_paths,
    reject_encoded_traversal_tokens,
    reject_host_drive_paths,
)
from fleet_rlm.utils.identity import sanitize_id

from .schemas import AttachmentRef

_SAFE_STORAGE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]")

# Conservative default for this slice; can be promoted to config later.
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class UploadSafetyError(ValueError):
    """Raised when an upload violates attachment safety policy."""


@dataclass(frozen=True)
class StagedUpload:
    attachment: AttachmentRef
    relative_path: str


def _sanitize_display_filename(raw_filename: str) -> str:
    name = (raw_filename or "").strip()
    if not name:
        raise UploadSafetyError("Filename is required.")
    try:
        reject_encoded_traversal_tokens(name)
        reject_backslash_paths(name)
        reject_host_drive_paths(name)
    except Exception as exc:
        raise UploadSafetyError(str(exc)) from exc
    if "/" in name:
        raise UploadSafetyError("Filename must be a simple basename.")
    candidate = name.strip().strip(".")
    if not candidate or ".." in candidate:
        raise UploadSafetyError("Filename must be a simple basename.")
    if len(candidate) > 255:
        raise UploadSafetyError("Filename is too long.")
    return candidate


def _sanitize_storage_filename(filename: str) -> str:
    cleaned = _SAFE_STORAGE_NAME_RE.sub("_", filename).strip("._")
    if not cleaned:
        cleaned = "file"
    return cleaned[:120]


def _uploads_session_root(session_id: str) -> str:
    safe_session_id = sanitize_id(session_id, "default-session")
    return f"uploads/sessions/{safe_session_id}/attachments"


def uploads_session_attachments_relative_dir(session_id: str) -> str:
    """Return the approved relative uploads directory for one session."""
    return _uploads_session_root(session_id)


def stage_uploaded_file_to_volume(
    *,
    volume_mount_path: str,
    session_id: str,
    filename: str,
    content_type: str | None,
    stream: BinaryIO,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> StagedUpload:
    """Stage a single uploaded file into the durable volume root.

    Returns an AttachmentRef with safe metadata and a safe relative staging path.
    """
    safe_filename = _sanitize_display_filename(filename)
    safe_content_type = (content_type or "").strip() or None
    if max_bytes < 1:
        raise UploadSafetyError("Upload size limit must be positive.")

    attachment_id = uuid.uuid4().hex
    storage_name = f"{attachment_id}__{_sanitize_storage_filename(safe_filename)}"
    relative_dir = _uploads_session_root(session_id)
    relative_path = f"{relative_dir}/{storage_name}"

    base = Path(volume_mount_path)
    dest_dir = base / relative_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    tmp_name = f".{storage_name}.{uuid.uuid4().hex}.tmp"
    tmp_path = dest_dir / tmp_name
    dest_path = dest_dir / storage_name

    hasher = hashlib.sha256()
    size_bytes = 0
    try:
        with open(tmp_path, "wb") as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise UploadSafetyError("Upload stream returned non-bytes.")
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise UploadSafetyError("File exceeds the upload size limit.")
                hasher.update(chunk)
                handle.write(chunk)
        os.replace(tmp_path, dest_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise

    attachment = AttachmentRef(
        id=attachment_id,
        filename=safe_filename,
        mime_type=safe_content_type,
        size_bytes=size_bytes,
        checksum=hasher.hexdigest(),
        staging_path=relative_path,
        metadata={},
    )
    return StagedUpload(
        attachment=attachment,
        relative_path=relative_path,
    )


__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "StagedUpload",
    "UploadSafetyError",
    "stage_uploaded_file_to_volume",
    "uploads_session_attachments_relative_dir",
]
