"""Committed Artifact HTTP retrieval contract."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.artifacts.local_catalog import LocalArtifactCatalog
from fleet_rlm.composition.testing import create_testing_app, host_roots
from fleet_rlm.config.settings import Settings


def test_api_get_committed_artifact_has_no_path_leak(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_artifact_bytes=2048, database_url=None)
    app = create_testing_app(settings=settings)
    scope = LocalScope()
    _, artifact_root = host_roots(settings)
    with TestClient(app) as client:
        ref = LocalArtifactCatalog(artifact_root, max_bytes=settings.max_artifact_bytes).create(
            user_id=scope.user_id,
            workspace_id=scope.workspace_id,
            session_id=uuid4(),
            run_id=uuid4(),
            kind="markdown",
            title="summary",
            content="## Result\n\nok",
        )
        response = client.get(f"/api/artifacts/{ref.id}")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {
            "id",
            "session_id",
            "run_id",
            "kind",
            "title",
            "media_type",
            "byte_size",
            "checksum_sha256",
        }
        dumped = json.dumps(body)
        assert "path" not in body
        assert "storage_key" not in dumped
        assert "/home/" not in dumped
        assert tmp_path.as_posix() not in dumped
        assert body["kind"] == "markdown"
        assert body["title"] == "summary"

        content = client.get(f"/api/artifacts/{ref.id}/content")
        assert content.status_code == 200
        assert content.content == b"## Result\n\nok"
        assert content.headers["content-type"].startswith("text/markdown")
        assert content.headers["content-length"] == str(ref.byte_size)
        assert content.headers["etag"] == f'"{ref.checksum_sha256}"'
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["content-disposition"] == 'attachment; filename="summary.md"'


def test_public_artifact_create_route_is_absent(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path), max_artifact_bytes=1024, database_url=None)
    app = create_testing_app(settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/artifacts",
            json={
                "session_id": str(uuid4()),
                "run_id": str(uuid4()),
                "kind": "text",
                "content": "artifact body",
                "title": "n",
            },
        )
    assert response.status_code == 404
