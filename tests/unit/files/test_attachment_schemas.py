from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.files import (
    AttachedFiles,
    AttachmentRef,
    AttachmentStagingRequest,
    AttachmentStagingResult,
)


def test_attachment_ref_validates_simple_file() -> None:
    ref = AttachmentRef(
        id="att-1",
        filename="notes.md",
        mime_type="text/markdown",
        size_bytes=12,
        checksum="sha256:abc",
    )

    assert ref.filename == "notes.md"
    assert ref.size_bytes == 12


@pytest.mark.parametrize("filename", ["../notes.md", "dir/notes.md", "dir\\notes.md", ""])
def test_attachment_ref_rejects_unsafe_filename(filename: str) -> None:
    with pytest.raises(ValidationError):
        AttachmentRef(id="att-1", filename=filename, size_bytes=1)


def test_attached_files_rejects_duplicate_ids() -> None:
    ref = AttachmentRef(id="att-1", filename="a.txt", size_bytes=1)

    with pytest.raises(ValidationError):
        AttachedFiles(attachments=[ref, ref])


def test_attachment_staging_shapes_validate() -> None:
    ref = AttachmentRef(id="att-1", filename="a.txt", size_bytes=1)
    request = AttachmentStagingRequest(attachment=ref, session_id="sess-1", content=b"a")
    result = AttachmentStagingResult(attachment=ref, sandbox_path="/home/daytona/memory/uploads/a.txt")

    assert request.attachment == ref
    assert result.sandbox_path.endswith("a.txt")
