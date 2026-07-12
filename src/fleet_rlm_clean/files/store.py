"""Production Attachment store: database catalog plus Workspace Volume bytes."""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix
from fleet_rlm_clean.daytona.workspace_volume import WorkspaceVolumeGateway
from fleet_rlm_clean.files.models import AttachmentRef
from fleet_rlm_clean.files.safety import sanitize_filename, validate_upload_size
from fleet_rlm_clean.persistence.repositories.files import SqlAlchemyAttachmentRepository, StoredAttachment


class VolumeAttachmentStore:
    def __init__(
        self,
        repository: SqlAlchemyAttachmentRepository,
        gateway: WorkspaceVolumeGateway,
        *,
        max_bytes: int,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._max_bytes = max_bytes
        self._paths = volume_paths or VolumePaths.from_mount()

    async def upload(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> AttachmentRef:
        validate_upload_size(len(data), max_bytes=self._max_bytes)
        attachment_id = uuid4()
        ref = AttachmentRef(
            id=attachment_id,
            filename=sanitize_filename(filename),
            content_type=content_type,
            byte_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
        )
        storage_ref = as_posix(self._paths.attachment_blob_path(attachment_id))
        await self._gateway.write_bytes(workspace_id, storage_ref, data)
        await self._repository.create(
            ref=ref,
            user_id=user_id,
            workspace_id=workspace_id,
            storage_ref=storage_ref,
        )
        return ref

    async def get(self, attachment_id: UUID, *, user_id: UUID, workspace_id: UUID) -> AttachmentRef:
        return (await self._repository.get(attachment_id, user_id=user_id, workspace_id=workspace_id)).ref

    async def get_stored(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
    ) -> StoredAttachment:
        """Return private metadata so a mounted Run Sandbox can stage bytes directly."""
        return await self._repository.get(attachment_id, user_id=user_id, workspace_id=workspace_id)

    async def read_bytes(self, attachment_id: UUID, *, user_id: UUID, workspace_id: UUID) -> bytes:
        stored = await self._repository.get(attachment_id, user_id=user_id, workspace_id=workspace_id)
        return await self._gateway.read_bytes(workspace_id, stored.storage_ref)


__all__ = ["VolumeAttachmentStore"]
