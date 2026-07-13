"""Fleet-controlled Attachment storage and Run staging paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fleet_rlm.daytona.paths import VolumePaths, as_posix
from fleet_rlm.files.models import AttachmentRun


@dataclass(frozen=True, slots=True)
class LocalAttachmentPathPolicy:
    """Opaque host-relative references for the hermetic Attachment catalog."""

    root: Path

    def attachment_blob(self, attachment_id: UUID) -> str:
        return f"{attachment_id}.bin"

    def run_attachment(self, run: AttachmentRun, attachment_id: UUID, filename: str) -> str:
        del run, filename
        return f"{attachment_id}.bin"


@dataclass(frozen=True, slots=True)
class DaytonaAttachmentPathPolicy:
    """Logical paths under the validated Workspace Volume layout."""

    paths: VolumePaths

    def attachment_blob(self, attachment_id: UUID) -> str:
        return as_posix(self.paths.attachment_blob_path(attachment_id))

    def run_attachment(self, run: AttachmentRun, attachment_id: UUID, filename: str) -> str:
        return as_posix(
            self.paths.run_attachment_file(
                run.session_id,
                run.run_id,
                attachment_id,
                filename,
            )
        )


__all__ = ["DaytonaAttachmentPathPolicy", "LocalAttachmentPathPolicy"]
