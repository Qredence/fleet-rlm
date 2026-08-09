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


def test_workspace_files_delete_and_patch_round_trip(tmp_path: Path) -> None:
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
            json={"path": "notes/report.md", "content": "hello world", "overwrite": False},
        )
        assert created.status_code == 200
        first_sha = created.json()["checksum_sha256"]

        patched = client.request(
            "PATCH",
            "/api/files/content",
            json={"path": "notes/report.md", "old": "world", "new": "fleet", "expected_sha256": first_sha},
        )
        assert patched.status_code == 200
        patched_body = patched.json()
        assert patched_body["path"] == "notes/report.md"
        assert patched_body["kind"] == "file"
        assert patched_body["byte_size"] == len(b"hello fleet")
        second_sha = hashlib.sha256(b"hello fleet").hexdigest()
        assert patched_body["checksum_sha256"] == second_sha  # chainable precondition token

        read = client.get("/api/files/content", params={"path": "notes/report.md"})
        assert read.status_code == 200
        assert read.json()["content"] == "hello fleet"

        deleted = client.request(
            "DELETE",
            "/api/files/content",
            json={"path": "notes/report.md", "expected_sha256": second_sha},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True, "path": "notes/report.md"}

        gone = client.get("/api/files/content", params={"path": "notes/report.md"})
        assert gone.status_code == 404
        assert gone.json()["code"] == "workspace_file_not_found"

        # The now-empty parent directory deletes cleanly too.
        emptied_notes = client.request("DELETE", "/api/files/content", json={"path": "notes"})
        assert emptied_notes.status_code == 200

        client.put("/api/files/content", json={"path": "empty/nested.txt", "content": "x", "overwrite": False})
        nested = client.request("DELETE", "/api/files/content", json={"path": "empty/nested.txt"})
        assert nested.status_code == 200
        emptied = client.request("DELETE", "/api/files/content", json={"path": "empty"})
        assert emptied.status_code == 200
        listing = client.get("/api/files")
        assert listing.status_code == 200
        assert listing.json()["entries"] == []


def test_workspace_files_delete_maps_404_and_409(tmp_path: Path) -> None:
    app = create_testing_app(
        settings=Settings(
            _env_file=None,
            run_environment="daytona",
            data_root=str(tmp_path),
        )
    )

    with TestClient(app) as client:
        missing = client.request("DELETE", "/api/files/content", json={"path": "missing.txt"})
        assert missing.status_code == 404
        assert missing.json()["code"] == "workspace_file_not_found"

        client.put("/api/files/content", json={"path": "notes/report.md", "content": "keep", "overwrite": False})
        stale = client.request(
            "DELETE",
            "/api/files/content",
            json={"path": "notes/report.md", "expected_sha256": hashlib.sha256(b"changed").hexdigest()},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "workspace_file_conflict"
        persisted = client.get("/api/files/content", params={"path": "notes/report.md"})
        assert persisted.json()["content"] == "keep"

        non_empty = client.request("DELETE", "/api/files/content", json={"path": "notes"})
        assert non_empty.status_code == 409
        assert non_empty.json()["code"] == "workspace_file_conflict"

        root_delete = client.request("DELETE", "/api/files/content", json={"path": "."})
        assert root_delete.status_code == 400

        for unsafe in ("../attachments/private", "/artifacts/private"):
            escaped = client.request("DELETE", "/api/files/content", json={"path": unsafe})
            assert escaped.status_code == 400


def test_workspace_files_patch_maps_404_409_and_returns_fresh_checksum(tmp_path: Path) -> None:
    app = create_testing_app(
        settings=Settings(
            _env_file=None,
            run_environment="daytona",
            data_root=str(tmp_path),
        )
    )

    with TestClient(app) as client:
        missing = client.request("PATCH", "/api/files/content", json={"path": "missing.txt", "old": "a", "new": "b"})
        assert missing.status_code == 404
        assert missing.json()["code"] == "workspace_file_not_found"

        client.put("/api/files/content", json={"path": "notes.md", "content": "dup dup", "overwrite": False})

        ambiguous = client.request("PATCH", "/api/files/content", json={"path": "notes.md", "old": "dup", "new": "x"})
        assert ambiguous.status_code == 409
        assert ambiguous.json()["code"] == "workspace_file_conflict"

        absent = client.request("PATCH", "/api/files/content", json={"path": "notes.md", "old": "nope", "new": "x"})
        assert absent.status_code == 409

        stale = client.request(
            "PATCH",
            "/api/files/content",
            json={
                "path": "notes.md",
                "old": "dup dup",
                "new": "done",
                "expected_sha256": hashlib.sha256(b"changed").hexdigest(),
            },
        )
        assert stale.status_code == 409
        persisted = client.get("/api/files/content", params={"path": "notes.md"})
        assert persisted.json()["content"] == "dup dup"

        replaced = client.request(
            "PATCH", "/api/files/content", json={"path": "notes.md", "old": "dup dup", "new": "done"}
        )
        assert replaced.status_code == 200
        assert replaced.json()["checksum_sha256"] == hashlib.sha256(b"done").hexdigest()

        client.put("/api/files/content", json={"path": "dir/file.txt", "content": "x", "overwrite": False})
        on_directory = client.request("PATCH", "/api/files/content", json={"path": "dir", "old": "a", "new": "b"})
        assert on_directory.status_code == 400
        assert on_directory.json()["code"] == "workspace_file_invalid"
