from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config import Settings


def test_independent_workspace_files_survive_requests_and_enforce_checksums(tmp_path: Path) -> None:
    app = create_testing_app(
        settings=Settings(
            _env_file=None,
            run_environment="daytona",
            data_root=str(tmp_path),
        )
    )

    with TestClient(app) as client:
        created = client.put(
            "/api/files/content",
            json={"path": "notes/report.md", "content": "first", "overwrite": False},
        )
        assert created.status_code == 200
        first_sha = hashlib.sha256(b"first").hexdigest()
        assert created.json()["checksum_sha256"] == first_sha

        read = client.get(
            "/api/files/content",
            params={"path": "notes/report.md", "max_chars": 3},
        )
        assert read.status_code == 200
        assert read.json()["content"] == "fir"
        assert read.json()["eof"] is False

        appended = client.post(
            "/api/files/append",
            json={
                "path": "notes/report.md",
                "content": " second",
                "expected_sha256": first_sha,
            },
        )
        assert appended.status_code == 200
        assert appended.json()["checksum_sha256"] == hashlib.sha256(b"first second").hexdigest()

        stale = client.put(
            "/api/files/content",
            json={
                "path": "notes/report.md",
                "content": "lost update",
                "overwrite": True,
                "expected_sha256": first_sha,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "workspace_file_conflict"

        persisted = client.get("/api/files/content", params={"path": "notes/report.md"})
        assert persisted.status_code == 200
        assert persisted.json()["content"] == "first second"


def test_workspace_files_api_cannot_address_fleet_managed_namespaces(tmp_path: Path) -> None:
    app = create_testing_app(
        settings=Settings(
            _env_file=None,
            run_environment="daytona",
            data_root=str(tmp_path),
        )
    )

    with TestClient(app) as client:
        for unsafe in (
            "../attachments/private",
            "/artifacts/private",
            "sessions/../../artifacts/private",
        ):
            response = client.put(
                "/api/files/content",
                json={"path": unsafe, "content": "x", "overwrite": False},
            )
            assert response.status_code == 400

        listed = client.get("/api/files")
        assert listed.status_code == 200
        assert listed.json()["entries"] == []
