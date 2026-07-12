"""Stage authorized attachments into a Fleet-controlled Sandbox path layout."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix, validate_path_id
from fleet_rlm_clean.files.models import StagedAttachment
from fleet_rlm_clean.files.uploads import LocalAttachmentStore


class AttachmentStager:
    """Copy blob into a session/run staging area; return logical Sandbox path only."""

    def __init__(
        self,
        store: LocalAttachmentStore,
        *,
        volume_paths: VolumePaths | None = None,
        host_stage_root: Path | str | None = None,
    ) -> None:
        self._store = store
        self._paths = volume_paths or VolumePaths.from_mount()
        # Optional host mirror for offline tests (simulates Volume FS).
        self._host_root = Path(host_stage_root) if host_stage_root else None

    def stage(
        self,
        attachment_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
    ) -> StagedAttachment:
        data = self._store.read_bytes(
            attachment_id, user_id=user_id, workspace_id=workspace_id
        )
        ref = self._store.get(attachment_id, user_id=user_id, workspace_id=workspace_id)
        # Logical path under fleet volume: sessions/{sid}/runs/{rid}/attachments/{aid}/{filename}
        sid = validate_path_id(session_id, label="session_id")
        rid = validate_path_id(run_id, label="run_id")
        aid = validate_path_id(attachment_id, label="attachment_id")
        run_dir = self._paths.run_dir(sid, rid)
        sandbox_path = as_posix(run_dir / "attachments" / aid / ref.filename)

        if self._host_root is not None:
            # Mirror only under host_stage_root; still return Sandbox logical path.
            dest = self._host_root.joinpath(*Path(sandbox_path).parts[1:])  # drop leading /
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        return StagedAttachment(attachment_id=attachment_id, sandbox_path=sandbox_path)
