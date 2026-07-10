"""Read-only resolution of staged attachment IDs to metadata-only AttachedFiles."""

from __future__ import annotations

import hmac
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from fleet_rlm.tools.paths import (
    FilesystemSafetyError,
    reject_backslash_paths,
    reject_encoded_traversal_tokens,
    reject_host_drive_paths,
    validate_relative_posix_path,
)

from .schemas import AttachedFiles, AttachmentRef
from .upload_staging import _ATTACHMENT_OWNER_MARKER, uploads_session_attachments_relative_dir

_ATTACHMENT_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class AttachmentResolutionError(ValueError):
    """Raised when attachment references cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class PersistedSessionOwnerProof:
    """Ownership evidence recovered from a session record, not a request payload."""

    session_id: str
    owner_scope: str


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
    owner_scope: str | None = None,
    persisted_session_owner_proof: PersistedSessionOwnerProof | None = None,
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
    safe_owner_scope = str(owner_scope or "").strip()
    if not safe_owner_scope:
        raise AttachmentResolutionError("Attachment owner scope is required.")

    safe_session_id = str(session_id).strip()
    normalized_ids = [_validate_attachment_id(item) for item in attachment_ids]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise AttachmentResolutionError("Duplicate attachment references are not allowed.")

    relative_dir = uploads_session_attachments_relative_dir(safe_session_id, owner_scope=safe_owner_scope)
    validate_relative_posix_path(relative_dir)

    base = Path(volume_mount_path)
    attachments_dir = base / relative_dir
    if not attachments_dir.is_dir():
        legacy_relative_dir = uploads_session_attachments_relative_dir(safe_session_id)
        validate_relative_posix_path(legacy_relative_dir)
        legacy_attachments_dir = base / legacy_relative_dir
        if not legacy_attachments_dir.is_dir():
            raise AttachmentResolutionError("One or more attachment references are invalid.")
        relative_dir = legacy_relative_dir
        attachments_dir = legacy_attachments_dir
    owner_marker = attachments_dir / _ATTACHMENT_OWNER_MARKER
    if owner_marker.is_file():
        try:
            marker_scope = owner_marker.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AttachmentResolutionError("One or more attachment references are invalid.") from exc
    else:
        # Phase 5 uploads created before the owner marker can only be resumed
        # when a persisted session record proves that the authenticated owner
        # owns this exact session. A request identity alone is not sufficient
        # proof for an unmarked shared-volume directory.
        proof = persisted_session_owner_proof
        proof_session_id = str(proof.session_id or "").strip() if proof is not None else ""
        proof_owner_scope = str(proof.owner_scope or "").strip() if proof is not None else ""
        if not (
            proof_session_id
            and proof_owner_scope
            and hmac.compare_digest(proof_session_id, safe_session_id)
            and hmac.compare_digest(proof_owner_scope, safe_owner_scope)
        ):
            raise AttachmentResolutionError("One or more attachment references are invalid.")
        marker_scope = proof_owner_scope
    if not marker_scope or not hmac.compare_digest(marker_scope, safe_owner_scope):
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
    "PersistedSessionOwnerProof",
    "resolve_attachment_refs",
]
