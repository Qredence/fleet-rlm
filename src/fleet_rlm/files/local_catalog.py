"""Hermetic Attachment catalog and blob adapters for AttachmentModule."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from fleet_rlm.files.errors import AttachmentNotFoundError
from fleet_rlm.files.lifecycle import StoredAttachment
from fleet_rlm.files.models import AttachmentAccess, AttachmentRef


class LocalAttachmentBlobGateway:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_ref: str) -> Path:
        path = (self.root / storage_ref).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".bin":
            raise ValueError("invalid Attachment storage reference")
        return path

    async def write(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        del workspace_id
        self._path(logical_path).write_bytes(data)

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        del workspace_id
        try:
            return self._path(logical_path).read_bytes()
        except FileNotFoundError as exc:
            raise AttachmentNotFoundError("attachment not found") from exc

    async def remove(self, workspace_id: UUID, logical_path: str) -> None:
        del workspace_id
        self._path(logical_path).unlink(missing_ok=True)


class LocalAttachmentCatalog:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, attachment_id: UUID) -> Path:
        return self.root / f"{attachment_id}.json"

    async def create(self, *, access: AttachmentAccess, ref: AttachmentRef, storage_ref: str) -> None:
        self._path(ref.id).write_text(
            json.dumps(
                {
                    "id": str(ref.id),
                    "user_id": str(access.user_id),
                    "workspace_id": str(access.workspace_id),
                    "filename": ref.filename,
                    "content_type": ref.content_type,
                    "byte_size": ref.byte_size,
                    "checksum_sha256": ref.checksum_sha256,
                    "storage_ref": storage_ref,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    async def get_many(
        self,
        *,
        access: AttachmentAccess,
        attachment_ids: Sequence[UUID],
    ) -> tuple[StoredAttachment, ...]:
        values: list[StoredAttachment] = []
        for attachment_id in attachment_ids:
            try:
                record = json.loads(self._path(attachment_id).read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise AttachmentNotFoundError("attachment not found") from exc
            if record["user_id"] != str(access.user_id) or record["workspace_id"] != str(access.workspace_id):
                raise AttachmentNotFoundError("attachment not found")
            values.append(
                StoredAttachment(
                    ref=AttachmentRef(
                        id=attachment_id,
                        filename=record["filename"],
                        content_type=record.get("content_type"),
                        byte_size=int(record["byte_size"]),
                        checksum_sha256=record["checksum_sha256"],
                    ),
                    storage_ref=record["storage_ref"],
                )
            )
        return tuple(values)


class WorkspaceAttachmentBlobGateway:
    """Adapt Workspace Volume byte I/O to AttachmentModule's blob port."""

    def __init__(self, gateway) -> None:
        self._gateway = gateway

    async def write(self, workspace_id: UUID, logical_path: str, data: bytes) -> None:
        await self._gateway.write_bytes(workspace_id, logical_path, data)

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        return await self._gateway.read_bytes(workspace_id, logical_path)

    async def remove(self, workspace_id: UUID, logical_path: str) -> None:
        await self._gateway.remove_bytes(workspace_id, logical_path)
