from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from fleet_rlm.files.upload_staging import (
    DEFAULT_MAX_UPLOAD_BYTES,
    UploadSafetyError,
    stage_uploaded_file_to_volume,
)


def test_stage_uploaded_file_writes_under_uploads_root(tmp_path: Path) -> None:
    staged = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id="sess-1",
        filename="hello.txt",
        content_type="text/plain",
        stream=BytesIO(b"hello world"),
    )

    assert staged.attachment.filename == "hello.txt"
    assert staged.attachment.size_bytes == 11
    assert staged.attachment.checksum
    assert staged.attachment.staging_path
    assert staged.relative_path == staged.attachment.staging_path
    assert staged.relative_path.startswith("uploads/sessions/")
    assert "/home/daytona/memory" not in staged.relative_path

    dest = tmp_path / staged.relative_path
    assert dest.is_file()
    assert dest.read_bytes() == b"hello world"


@pytest.mark.parametrize(
    "filename",
    [
        "../x.txt",
        "..\\x.txt",
        "/abs/path.txt",
        "%2e%2e%2fsecret.txt",
        "C:\\x.txt",
        "a/b.txt",
    ],
)
def test_stage_uploaded_file_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    with pytest.raises(UploadSafetyError):
        stage_uploaded_file_to_volume(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            filename=filename,
            content_type="text/plain",
            stream=BytesIO(b"hi"),
        )


def test_stage_uploaded_file_rejects_oversize(tmp_path: Path) -> None:
    with pytest.raises(UploadSafetyError):
        stage_uploaded_file_to_volume(
            volume_mount_path=str(tmp_path),
            session_id="sess-1",
            filename="big.bin",
            content_type="application/octet-stream",
            stream=BytesIO(b"a" * (DEFAULT_MAX_UPLOAD_BYTES + 1)),
        )


def test_stage_uploaded_file_does_not_overwrite_existing(tmp_path: Path) -> None:
    first = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id="sess-1",
        filename="same.txt",
        content_type="text/plain",
        stream=BytesIO(b"first"),
    )
    second = stage_uploaded_file_to_volume(
        volume_mount_path=str(tmp_path),
        session_id="sess-1",
        filename="same.txt",
        content_type="text/plain",
        stream=BytesIO(b"second"),
    )

    assert first.attachment.id != second.attachment.id
    assert first.relative_path != second.relative_path

    first_path = tmp_path / first.relative_path
    second_path = tmp_path / second.relative_path
    assert first_path.read_bytes() == b"first"
    assert second_path.read_bytes() == b"second"
