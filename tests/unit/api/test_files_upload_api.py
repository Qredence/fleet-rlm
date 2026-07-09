from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.dependencies import require_http_identity
from fleet_rlm.files.upload_staging import DEFAULT_MAX_UPLOAD_BYTES


@pytest.fixture
def files_client(no_db_app, monkeypatch, stub_identity, tmp_path: Path) -> Iterator[TestClient]:
    from joserfc.jwk import KeySet

    from fleet_rlm.api.auth.neon import NeonAuthProvider
    from fleet_rlm.api.routers import files as files_router

    monkeypatch.setattr(NeonAuthProvider, "_fetch_jwks", lambda self: KeySet([]))
    monkeypatch.setattr(files_router, "DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH", tmp_path)

    app = no_db_app
    app.dependency_overrides[require_http_identity] = lambda: stub_identity  # type: ignore[assignment]

    with TestClient(app) as client:
        yield client


def test_upload_file_succeeds_and_returns_safe_metadata(files_client: TestClient, tmp_path: Path) -> None:
    response = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "attachment" in body
    assert "uploaded" in body

    attachment = body["attachment"]
    assert attachment["filename"] == "hello.txt"
    assert attachment["size_bytes"] == 5
    assert attachment["checksum"]
    assert attachment["staging_path"].startswith("uploads/sessions/")

    # No raw host paths
    payload = response.text
    assert "/Users/" not in payload
    assert "/Volumes/" not in payload
    assert "C:\\" not in payload

    # No raw volume mount paths
    assert "/home/daytona/memory" not in payload
    assert str(tmp_path) not in payload

    dest = tmp_path / attachment["staging_path"]
    assert dest.is_file()
    assert dest.read_bytes() == b"hello"


@pytest.mark.parametrize(
    "filename",
    [
        "../x.txt",
        "..\\x.txt",
        "/abs/path.txt",
        "%2e%2e%2fsecret.txt",
    ],
)
def test_upload_rejects_unsafe_filenames(files_client: TestClient, filename: str) -> None:
    response = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": (filename, b"hi", "text/plain")},
    )

    assert response.status_code == 400, response.text


def test_upload_rejects_oversize(files_client: TestClient) -> None:
    response = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": ("big.bin", b"a" * (DEFAULT_MAX_UPLOAD_BYTES + 1), "application/octet-stream")},
    )

    assert response.status_code == 400, response.text


def test_upload_does_not_overwrite_duplicate_names(files_client: TestClient, tmp_path: Path) -> None:
    first = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": ("same.txt", b"first", "text/plain")},
    )
    second = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": ("same.txt", b"second", "text/plain")},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    a1 = first.json()["attachment"]
    a2 = second.json()["attachment"]
    assert a1["id"] != a2["id"]
    assert a1["staging_path"] != a2["staging_path"]

    p1 = tmp_path / a1["staging_path"]
    p2 = tmp_path / a2["staging_path"]
    assert p1.read_bytes() == b"first"
    assert p2.read_bytes() == b"second"


def test_upload_requires_auth_when_enabled(files_client: TestClient) -> None:
    # Enable real auth and clear overrides so the auth dependency rejects.
    files_client.app.state.config_deps.config.auth_required = True
    files_client.app.dependency_overrides.pop(require_http_identity, None)

    response = files_client.post(
        "/api/v1/files/upload",
        data={"session_id": "sess-1"},
        files={"file": ("hello.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 401, response.text
