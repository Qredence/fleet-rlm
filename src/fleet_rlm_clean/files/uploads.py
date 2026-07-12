"""Atomic local blob store for attachment bytes (offline / pre-Volume host cache)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fleet_rlm_clean.files.errors import AttachmentNotFoundError
from fleet_rlm_clean.files.models import AttachmentRef
from fleet_rlm_clean.files.safety import sanitize_filename, validate_upload_size


class LocalAttachmentStore:
    """Store attachment blobs + metadata under a host root (never exposed publicly)."""

    def __init__(self, root: Path | str, *, max_bytes: int) -> None:
        self.root = Path(root)
        self.max_bytes = max_bytes
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
        }
        meta.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
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
        self.get(attachment_id, user_id=user_id, workspace_id=workspace_id)
        blob = self._blob_path(attachment_id)
        if not blob.is_file():
            raise AttachmentNotFoundError("attachment not found")
        return blob.read_bytes()
