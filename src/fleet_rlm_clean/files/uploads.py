"""Atomic local blob store for attachment bytes (offline catalog + Volume promote)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix
from fleet_rlm_clean.daytona.volume_fs import VolumeBlobFs
from fleet_rlm_clean.files.errors import AttachmentNotFoundError
from fleet_rlm_clean.files.models import AttachmentRef
from fleet_rlm_clean.files.safety import sanitize_filename, validate_upload_size


class LocalAttachmentStore:
    """Store attachment catalog under a host root; optionally promote into Volume scope.

    Host ``root`` remains a hermetic offline catalog. When ``volume_fs`` is set,
    durable blob+meta are also written under ``VolumePaths`` attachment layout so
    Workspace Volume Scope is the durable bytes location for Runs.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_bytes: int,
        volume_fs: VolumeBlobFs | None = None,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
        self._volume_fs = volume_fs
        self._paths = volume_paths or VolumePaths.from_mount()
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, attachment_id: UUID) -> Path:
        return self.root / f"{attachment_id}.meta.json"

    def _blob_path(self, attachment_id: UUID) -> Path:
        return self.root / f"{attachment_id}.bin"

    def upload(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> AttachmentRef:
        validate_upload_size(len(data), max_bytes=self.max_bytes)
        safe_name = sanitize_filename(filename)
        attachment_id = uuid4()
        checksum = hashlib.sha256(data).hexdigest()

        blob = self._blob_path(attachment_id)
        meta = self._meta_path(attachment_id)
        # Atomic write: temp then rename
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=".up-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, blob)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

        volume_blob = as_posix(self._paths.attachment_blob_path(attachment_id))
        volume_meta = as_posix(self._paths.attachment_meta_path(attachment_id))
        record = {
            "id": str(attachment_id),
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "filename": safe_name,
            "content_type": content_type,
            "byte_size": len(data),
            "checksum_sha256": checksum,
            # Private host-relative key — never return in API
            "storage_key": f"{attachment_id}.bin",
            "volume_blob_path": volume_blob,
        }
        meta.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

        if self._volume_fs is not None:
            self._volume_fs.write_bytes(volume_blob, data)
            self._volume_fs.write_bytes(
                volume_meta,
                (json.dumps(record, indent=2) + "\n").encode("utf-8"),
            )

        return AttachmentRef(
            id=attachment_id,
            filename=safe_name,
            content_type=content_type,
            byte_size=len(data),
            checksum_sha256=checksum,
        )

    def _load_record(self, attachment_id: UUID) -> dict[str, Any]:
        path = self._meta_path(attachment_id)
        if not path.is_file():
            raise AttachmentNotFoundError("attachment not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def get(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> AttachmentRef:
        record = self._load_record(attachment_id)
        if UUID(record["user_id"]) != user_id or UUID(record["workspace_id"]) != workspace_id:
            # Do not leak existence across workspaces
            raise AttachmentNotFoundError("attachment not found")
        return AttachmentRef(
            id=UUID(record["id"]),
            filename=record["filename"],
            content_type=record.get("content_type"),
            byte_size=int(record["byte_size"]),
            checksum_sha256=record["checksum_sha256"],
        )

    def read_bytes(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> bytes:
        record = self._load_record(attachment_id)
        if UUID(record["user_id"]) != user_id or UUID(record["workspace_id"]) != workspace_id:
            raise AttachmentNotFoundError("attachment not found")
        # Prefer Workspace Volume Scope durable blob when present.
        volume_blob = record.get("volume_blob_path")
        if self._volume_fs is not None and isinstance(volume_blob, str) and self._volume_fs.exists(volume_blob):
            return self._volume_fs.read_bytes(volume_blob)
        blob = self._blob_path(attachment_id)
        if not blob.is_file():
            raise AttachmentNotFoundError("attachment not found")
        return blob.read_bytes()

    def durable_volume_blob_path(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> str:
        """Internal: Fleet logical durable Attachment path after reauth."""
        record = self._load_record(attachment_id)
        if UUID(record["user_id"]) != user_id or UUID(record["workspace_id"]) != workspace_id:
            raise AttachmentNotFoundError("attachment not found")
        path = record.get("volume_blob_path")
        if isinstance(path, str) and path:
            return path
        return as_posix(self._paths.attachment_blob_path(attachment_id))
