"""Attachment upload and retrieval HTTP contract."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.attachments.errors import AttachmentStorageError
from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config.settings import Settings


def test_api_upload_get_has_no_path_leak(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=1024, database_url=None)
    app = create_testing_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/attachments",
            files={"attachment": ("readme.md", b"# hi", "text/markdown")},
        )
        assert response.status_code == 201
        body = response.json()
        assert set(body.keys()) == {
            "id",
            "filename",
            "content_type",
            "byte_size",
            "checksum_sha256",
        }
        assert "path" not in body
        assert "/home/" not in json.dumps(body)
        assert tmp_path.as_posix() not in json.dumps(body)

        file_id = body["id"]
        got = client.get(f"/api/attachments/{file_id}")
        assert got.status_code == 200
        assert got.json()["filename"] == "readme.md"

        staged = client.post(
            f"/api/attachments/{file_id}/stage",
            json={"session_id": str(uuid4()), "run_id": str(uuid4())},
        )
        assert staged.status_code == 404


def test_api_rejects_oversize(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=4, database_url=None)
    app = create_testing_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/attachments",
            files={"attachment": ("big.bin", b"12345", "application/octet-stream")},
        )
    assert response.status_code == 400


def test_api_storage_failure_is_unavailable_not_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient blob-write failure is a server fault (503), not client input (400)."""
    from fleet_rlm.attachments import local_catalog

    async def failing_write(*_args: object, **_kwargs: object) -> None:
        raise AttachmentStorageError("Attachment storage is unavailable")

    monkeypatch.setattr(local_catalog.LocalAttachmentBlobGateway, "write_bytes", failing_write)
    settings = Settings(data_root=str(tmp_path), max_upload_bytes=1024, database_url=None)
    app = create_testing_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/attachments",
            files={"attachment": ("phase1.txt", b"phase-one capsule witness: CEDAR-17\n", "text/plain")},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "attachment_unavailable"
