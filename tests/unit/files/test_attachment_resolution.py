from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from fleet_rlm.files.attachment_resolution import AttachmentResolutionError, resolve_attachment_refs
from fleet_rlm.files.upload_staging import stage_uploaded_file_to_volume


def _stage(tmp_path: Path, session_id: str, filename: str = "hello.txt") -> str:
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id=session_id,
        filename=filename,
        content_type="text/plain",
        stream=BytesIO(b"hello"),
    )
    return staged.attachment.id


def test_resolve_attachment_refs_returns_metadata_only(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    resolved = resolve_attachment_refs(
        volume_mount_path=str(tmp_path),
        session_id="sess-1",
        attachment_ids=[attachment_id],
    )

    assert resolved is not None
    assert len(resolved.attachments) == 1
    attachment = resolved.attachments[0]
    assert attachment.id == attachment_id
    assert attachment.filename == "hello.txt"
    assert attachment.size_bytes == 5
    assert attachment.staging_path is not None
    assert attachment.staging_path.startswith("uploads/sessions/")
    assert "/home/daytona/memory" not in attachment.staging_path
    assert attachment.checksum is None


def test_resolve_attachment_refs_returns_none_for_empty_list(tmp_path: Path) -> None:
    assert (
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=[],
        )
        is None
    )


def test_resolve_attachment_refs_requires_session_id(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="session_id is required"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id=None,
            attachment_ids=[attachment_id],
        )


def test_resolve_attachment_refs_rejects_unknown_id(tmp_path: Path) -> None:
    _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=["0" * 32],
        )


def test_resolve_attachment_refs_rejects_wrong_session(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-2",
            attachment_ids=[attachment_id],
        )


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "../" + ("a" * 32),
        ("a" * 32) + "/x",
        ("a" * 32) + "\\x",
        "%2e%2e%2f" + ("a" * 28),
        "C:" + ("a" * 30),
        "not-hex-id",
        "a" * 31,
    ],
)
def test_resolve_attachment_refs_rejects_unsafe_ids(tmp_path: Path, bad_id: str) -> None:
    with pytest.raises(AttachmentResolutionError):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=[bad_id],
        )


def test_resolve_attachment_refs_rejects_duplicate_ids(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="Duplicate"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=[attachment_id, attachment_id],
        )
