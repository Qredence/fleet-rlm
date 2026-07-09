"""Read-only resolution of staged attachment IDs to metadata-only AttachedFiles."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fleet_rlm.tools.paths import (
    FilesystemSafetyError,
    reject_backslash_paths,
    reject_encoded_traversal_tokens,
    reject_host_drive_paths,
    validate_relative_posix_path,
)

from .schemas import AttachedFiles, AttachmentRef
from .upload_staging import uploads_session_attachments_relative_dir

_ATTACHMENT_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class AttachmentResolutionError(ValueError):
    """Raised when attachment references cannot be resolved safely."""


def _validate_attachment_id(attachment_id: str) -> str:
    raw = str(attachment_id or "").strip()
    if not raw:
        raise AttachmentResolutionError("Invalid attachment reference.")
    try:
        reject_encoded_traversal_tokens(raw)
        reject_backslash_paths(raw)
        reject_host_drive_paths(raw)
    except FilesystemSafetyError as exc:
        raise AttachmentResolutionError("Invalid attachment reference.") from exc
    if "/" in raw or "\\" in raw or ".." in raw:
        raise AttachmentResolutionError("Invalid attachment reference.")
    if not _ATTACHMENT_ID_RE.fullmatch(raw):
        raise AttachmentResolutionError("Invalid attachment reference.")
    return raw


def _display_filename_from_storage_name(storage_name: str, attachment_id: str) -> str:
    prefix = f"{attachment_id}__"
    if storage_name.startswith(prefix):
        return storage_name[len(prefix) :] or "file"
    return storage_name


def _guess_mime_type(filename: str) -> str | None:
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed


def resolve_attachment_refs(
    *,
    volume_mount_path: str,
    session_id: str | None,
    attachment_ids: list[str] | None,
) -> AttachedFiles | None:
    """Resolve attachment IDs to metadata-only ``AttachedFiles``.

    Returns ``None`` when *attachment_ids* is empty or ``None``. Raises
    ``AttachmentResolutionError`` for invalid IDs, missing session scope, or
    unknown attachments. Does not read file contents.
    """
    if not attachment_ids:
        return None

    if not str(session_id or "").strip():
        raise AttachmentResolutionError("session_id is required when attachment_refs is provided.")

    safe_session_id = str(session_id).strip()
    normalized_ids = [_validate_attachment_id(item) for item in attachment_ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise AttachmentResolutionError("Duplicate attachment references are not allowed.")

    relative_dir = uploads_session_attachments_relative_dir(safe_session_id)
    validate_relative_posix_path(relative_dir)

    base = Path(volume_mount_path)
    attachments_dir = base / relative_dir
    if not attachments_dir.is_dir():
        raise AttachmentResolutionError("One or more attachment references are invalid.")

    resolved: list[AttachmentRef] = []
    for attachment_id in normalized_ids:
        matches = sorted(
            path for path in attachments_dir.iterdir() if path.is_file() and path.name.startswith(f"{attachment_id}__")
        )
        if not matches:
            raise AttachmentResolutionError("One or more attachment references are invalid.")
        if len(matches) > 1:
            raise AttachmentResolutionError("One or more attachment references are invalid.")

        storage_path = matches[0]
        relative_path = f"{relative_dir}/{storage_path.name}"
        validate_relative_posix_path(relative_path)
        display_name = _display_filename_from_storage_name(storage_path.name, attachment_id)
        stat = storage_path.stat()
        resolved.append(
            AttachmentRef(
                id=attachment_id,
                filename=display_name,
                mime_type=_guess_mime_type(display_name),
                size_bytes=max(0, int(stat.st_size)),
                checksum=None,
                staging_path=relative_path,
                metadata={},
            )
        )

    return AttachedFiles(attachments=resolved)


__all__ = [
    "AttachmentResolutionError",
    "resolve_attachment_refs",
]
