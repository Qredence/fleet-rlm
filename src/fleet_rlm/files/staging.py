"""Stage authorized attachments into Workspace Volume Scope (Run-scoped paths)."""

from __future__ import annotations

from uuid import UUID

from fleet_rlm.daytona.paths import VolumePaths, as_posix
from fleet_rlm.daytona.volume_fs import VolumeBlobFs
from fleet_rlm.files.models import StagedAttachment
from fleet_rlm.files.uploads import LocalAttachmentStore


class AttachmentStager:
    """Materialize Attachment bytes into a Fleet-controlled Run path on the Volume.

    Staging always writes through ``volume_fs`` so Sandbox / Host-Mediated Tools
    can read under Workspace Volume Scope. Optional host-only skip is rejected.
    """

    def __init__(
        self,
        store: LocalAttachmentStore,
        *,
        volume_fs: VolumeBlobFs,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._store = store
        self._volume_fs = volume_fs
        self._paths = volume_paths or VolumePaths.from_mount()

    def stage(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
    ) -> StagedAttachment:
        data = self._store.read_bytes(attachment_id, user_id=user_id, workspace_id=workspace_id)
        ref = self._store.get(attachment_id, user_id=user_id, workspace_id=workspace_id)
        sandbox_path = as_posix(
            self._paths.run_attachment_file(
                session_id,
                run_id,
                attachment_id,
                ref.filename,
            )
        )
        self._volume_fs.write_bytes(sandbox_path, data)
        return StagedAttachment(attachment_id=attachment_id, sandbox_path=sandbox_path)
