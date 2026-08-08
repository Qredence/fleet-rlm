from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

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


def test_volume_tree_returns_relative_logical_paths(tmp_path: Path) -> None:
    app = create_testing_app(settings=Settings(_env_file=None, run_environment="daytona", data_root=str(tmp_path)))
    workspace_id = uuid5(NAMESPACE_URL, "fleet-rlm/local-workspace")

    import asyncio

    with TestClient(app) as client:
        gateway = app.state.runtime_inventory.workspace_volume_gateway
        assert gateway is not None
        asyncio.run(gateway.write_bytes(workspace_id, "/home/daytona/fleet/sessions/a/turn.json", b"{}"))
        response = client.get("/api/volume/tree")
        assert response.status_code == 200
        assert response.json() == {
            "paths": ["sessions/a/turn.json"],
            "directories": ["artifacts", "attachments", "files", "projects", "sessions"],
            "truncated": False,
        }


def test_volume_tree_is_not_truncated_when_file_count_equals_requested_limit(tmp_path: Path) -> None:
    app = create_testing_app(settings=Settings(_env_file=None, run_environment="daytona", data_root=str(tmp_path)))
    workspace_id = uuid5(NAMESPACE_URL, "fleet-rlm/local-workspace")

    import asyncio

    with TestClient(app) as client:
        gateway = app.state.runtime_inventory.workspace_volume_gateway
        assert gateway is not None
        asyncio.run(gateway.write_bytes(workspace_id, "/home/daytona/fleet/sessions/a/turn.json", b"{}"))
        response = client.get("/api/volume/tree", params={"max_files": 1})

        assert response.status_code == 200
        assert response.json()["paths"] == ["sessions/a/turn.json"]
        assert response.json()["truncated"] is False


def test_workspace_files_stat_reports_content_checksum_and_directory_entries(tmp_path: Path) -> None:
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

        file_stat = client.get("/api/files/stat", params={"path": "notes/report.md"})
        assert file_stat.status_code == 200
        file_body = file_stat.json()
        assert file_body["path"] == "notes/report.md"
        assert file_body["kind"] == "file"
        assert file_body["byte_size"] == 5
        assert file_body["checksum_sha256"] == hashlib.sha256(b"first").hexdigest()

        directory_stat = client.get("/api/files/stat", params={"path": "notes"})
        assert directory_stat.status_code == 200
        directory_body = directory_stat.json()
        assert directory_body["kind"] == "directory"
        assert directory_body["byte_size"] is None
        assert directory_body["checksum_sha256"] is None

        root_stat = client.get("/api/files/stat", params={"path": "."})
        assert root_stat.status_code == 200
        root_body = root_stat.json()
        assert root_body["path"] == "."
        assert root_body["kind"] == "directory"
        assert root_body["checksum_sha256"] is None

        missing = client.get("/api/files/stat", params={"path": "missing.txt"})
        assert missing.status_code == 404
        assert missing.json()["code"] == "workspace_file_not_found"
