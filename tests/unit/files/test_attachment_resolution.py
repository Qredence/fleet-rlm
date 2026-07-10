from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from fleet_rlm.files.attachment_resolution import (
    AttachmentResolutionError,
    PersistedSessionOwnerProof,
    resolve_attachment_refs,
)
from fleet_rlm.files.upload_staging import stage_uploaded_file_to_volume

_OWNER_SCOPE = "tenant-a:user-a"


def _stage(tmp_path: Path, session_id: str, filename: str = "hello.txt") -> str:
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id=session_id,
        filename=filename,
        content_type="text/plain",
        stream=BytesIO(b"hello"),
        owner_scope=_OWNER_SCOPE,
    )
    return staged.attachment.id


def _stage_as_unmarked_legacy_upload(tmp_path: Path, session_id: str, filename: str = "hello.txt") -> str:
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id=session_id,
        filename=filename,
        content_type="text/plain",
        stream=BytesIO(b"hello"),
        owner_scope=_OWNER_SCOPE,
    )
    source = tmp_path / staged.relative_path
    legacy_dir = tmp_path / f"uploads/sessions/{session_id}/attachments"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    source.rename(legacy_dir / source.name)
    source.parent.joinpath(".attachment-owner").unlink()
    source.parent.rmdir()
    return staged.attachment.id


def test_resolve_attachment_refs_returns_metadata_only(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    resolved = resolve_attachment_refs(
        volume_mount_path=str(tmp_path),
        session_id="sess-1",
        attachment_ids=[attachment_id],
        owner_scope=_OWNER_SCOPE,
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
            owner_scope=_OWNER_SCOPE,
        )


def test_resolve_attachment_refs_rejects_unknown_id(tmp_path: Path) -> None:
    _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=["0" * 32],
            owner_scope=_OWNER_SCOPE,
        )


def test_resolve_attachment_refs_rejects_wrong_session(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-2",
            attachment_ids=[attachment_id],
            owner_scope=_OWNER_SCOPE,
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
            owner_scope=_OWNER_SCOPE,
        )


def test_resolve_attachment_refs_rejects_a_different_authenticated_owner(tmp_path: Path) -> None:
    attachment_id = _stage(tmp_path, "sess-1")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            attachment_ids=[attachment_id],
            owner_scope="tenant-b:user-b",
        )


def test_resolve_same_session_id_reads_only_the_authenticated_owner_namespace(tmp_path: Path) -> None:
    first_id = _stage(tmp_path, "shared-session-id", "first.txt")
    second = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id="shared-session-id",
        filename="second.txt",
        content_type="text/plain",
        stream=BytesIO(b"second"),
        owner_scope="tenant-b:user-b",
    )

    first = resolve_attachment_refs(
        volume_mount_path=str(tmp_path),
        session_id="shared-session-id",
        attachment_ids=[first_id],
        owner_scope=_OWNER_SCOPE,
    )
    assert first is not None
    assert first.attachments[0].id == first_id

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="shared-session-id",
            attachment_ids=[second.attachment.id],
            owner_scope=_OWNER_SCOPE,
        )


def test_resolve_legacy_upload_requires_canonical_session_owner_proof(tmp_path: Path) -> None:
    attachment_id = _stage_as_unmarked_legacy_upload(tmp_path, "legacy-session")

    resolved = resolve_attachment_refs(
        volume_mount_path=str(tmp_path),
        session_id="legacy-session",
        attachment_ids=[attachment_id],
        owner_scope=_OWNER_SCOPE,
        persisted_session_owner_proof=PersistedSessionOwnerProof(
            session_id="legacy-session",
            owner_scope=_OWNER_SCOPE,
        ),
    )

    assert resolved is not None
    assert resolved.attachments[0].id == attachment_id


def test_resolve_legacy_upload_rejects_unproven_or_cross_owner_access(tmp_path: Path) -> None:
    attachment_id = _stage_as_unmarked_legacy_upload(tmp_path, "legacy-session")

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="legacy-session",
            attachment_ids=[attachment_id],
            owner_scope="tenant-b:user-b",
            persisted_session_owner_proof=PersistedSessionOwnerProof(
                session_id="legacy-session",
                owner_scope=_OWNER_SCOPE,
            ),
        )

    with pytest.raises(AttachmentResolutionError, match="invalid"):
        resolve_attachment_refs(
            volume_mount_path=str(tmp_path),
            session_id="legacy-session",
            attachment_ids=[attachment_id],
            owner_scope=_OWNER_SCOPE,
        )
