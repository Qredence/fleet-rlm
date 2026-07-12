"""B5: Attachment / Artifact durability under Workspace Volume Scope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.app import create_app
from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.paths import UnsafePathError, VolumePaths, as_posix
from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.uploads import LocalAttachmentStore


def test_volume_paths_durable_attachment_and_artifact_layout() -> None:
    paths = VolumePaths.from_mount()
    aid = uuid4()
    art = uuid4()
    sid, rid = uuid4(), uuid4()
    assert as_posix(paths.attachment_blob_path(aid)).endswith(f"/attachments/{aid}/blob")
    assert as_posix(paths.artifact_blob_path(art)).endswith(f"/artifacts/{art}/blob")
    staged = paths.run_attachment_file(sid, rid, aid, "note.txt")
    assert as_posix(staged).startswith("/home/daytona/fleet/sessions/")
    assert str(aid) in as_posix(staged)


def test_upload_promotes_durable_blob_into_workspace_volume_scope(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalAttachmentStore(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    user, ws = uuid4(), uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="a.txt",
        content_type="text/plain",
        data=b"durable-bytes",
    )
    durable = store.durable_volume_blob_path(ref.id, user_id=user, workspace_id=ws)
    assert durable.startswith("/home/daytona/fleet/attachments/")
    assert mirror.exists(durable)
    assert mirror.read_bytes(durable) == b"durable-bytes"
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == b"durable-bytes"


def test_stager_requires_volume_write_and_materializes_run_path(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalAttachmentStore(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    stager = AttachmentStager(store, volume_fs=mirror, volume_paths=mirror.volume_paths)
    user, ws = uuid4(), uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="in.txt",
        content_type="text/plain",
        data=b"stage-me",
    )
    session_id, run_id = uuid4(), uuid4()
    staged = stager.stage(
        ref.id,
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
    )
    assert "/runs/" in staged.sandbox_path
    assert mirror.read_bytes(staged.sandbox_path) == b"stage-me"


def test_artifact_store_writes_durable_and_run_scoped_volume_bytes(tmp_path: Path) -> None:
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalArtifactStore(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    content = "hello artifact"
    ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        content=content,
        title="t",
    )
    expected = content.encode("utf-8")
    checksum = hashlib.sha256(expected).hexdigest()
    assert ref.checksum_sha256 == checksum
    durable = store.durable_volume_blob_path(ref.id, user_id=user, workspace_id=ws)
    run_path = store.sandbox_path_for(ref.id, user_id=user, workspace_id=ws)
    assert mirror.read_bytes(durable) == expected
    assert mirror.read_bytes(run_path) == expected
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == expected


def test_artifact_survives_catalog_delete_when_volume_blob_present(tmp_path: Path) -> None:
    """Volume Scope bytes remain readable after host catalog blob is removed."""
    mirror = HostVolumeMirror(tmp_path / "volume")
    store = LocalArtifactStore(
        tmp_path / "catalog",
        max_bytes=1024,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    user, ws = uuid4(), uuid4()
    ref = store.create(
        user_id=user,
        workspace_id=ws,
        session_id=uuid4(),
        run_id=uuid4(),
        kind="text",
        content="persist-me",
    )
    # Simulate host catalog blob loss while Volume Scope remains (Sandbox replace case).
    store._blob_path(ref.id).unlink()  # noqa: SLF001
    assert store.read_bytes(ref.id, user_id=user, workspace_id=ws) == b"persist-me"


def test_public_attachment_and_artifact_responses_omit_paths(tmp_path: Path) -> None:
    settings = Settings(
        upload_root=str(tmp_path / "uploads"),
        artifact_root=str(tmp_path / "artifacts"),
        max_upload_bytes=1024,
        max_artifact_bytes=1024,
    )
    app = create_app(settings=settings)
    user, ws = uuid4(), uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(ws),
    }
    client = TestClient(app)
    up = client.post(
        "/api/files",
        headers=headers,
        files={"file": ("x.txt", b"abc", "text/plain")},
    )
    assert up.status_code == 200
    up_body = up.json()
    assert "path" not in up_body
    assert "/home/" not in json.dumps(up_body)
    assert str(tmp_path) not in json.dumps(up_body)

    # Host-Mediated creation is the only foundation path; public create is absent.
    art = client.post(
        "/api/artifacts",
        headers=headers,
        json={
            "session_id": str(uuid4()),
            "run_id": str(uuid4()),
            "kind": "text",
            "content": "artifact body",
            "title": "n",
        },
    )
    assert art.status_code == 404


def test_host_volume_mirror_rejects_escape(tmp_path: Path) -> None:

    mirror = HostVolumeMirror(tmp_path / "volume")
    with pytest.raises(UnsafePathError):
        mirror.write_bytes("/etc/passwd", b"nope")
