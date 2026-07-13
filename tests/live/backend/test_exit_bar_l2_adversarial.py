"""Exit-bar L2 — tip-matched adversarial workspace isolation.

Gate: FLEET_LIVE=1
Workspace B must not access Workspace A session/attachment/artifact/run;
Volume mounts must use distinct Workspace Volume Scope subpaths.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm.app import create_live_app
from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.chat.live_context import resolve_settings
from fleet_rlm.config import Settings
from fleet_rlm.daytona.paths import volume_paths_from_settings
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.daytona.volumes import workspace_volume_subpath
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona]

_EVIDENCE = Path(".scratch/clean-backend-refoundation/assets/live-adversarial-isolation-evidence.json")


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("FLEET_DAYTONA_API_KEY"))


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for exit-bar L2")
    if not _have_daytona():
        pytest.skip("Daytona API key not configured")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _lockfile_fingerprint() -> str:
    lock = Path("uv.lock")
    if not lock.exists():
        return "missing-uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def _hdr(user_id: UUID, workspace_id: UUID) -> dict[str, str]:
    return {
        "X-Fleet-User-Id": str(user_id),
        "X-Fleet-Workspace-Id": str(workspace_id),
    }


@pytest.mark.timeout(600)
def test_exit_bar_l2_adversarial_isolation(tmp_path: Path) -> None:
    _skip_unless_live()

    db_path = tmp_path / "l2.sqlite"
    upload_root = tmp_path / "uploads"
    artifact_root = tmp_path / "artifacts"
    upload_root.mkdir()
    artifact_root.mkdir()

    base = resolve_settings(Settings())
    daytona_key = base.daytona_api_key.get_secret_value() if base.daytona_api_key is not None else ""
    llm_key = base.llm_api_key.get_secret_value() if base.llm_api_key is not None else "unused-for-l2"
    settings = base.model_copy(
        update={
            "live_kernel": True,
            "auth_mode": "dev",
            "database_url": f"sqlite+aiosqlite:///{db_path.resolve()}",
            "upload_root": str(upload_root),
            "artifact_root": str(artifact_root),
            "daytona_api_key": SecretStr(daytona_key),
            "llm_api_key": SecretStr(llm_key),
        }
    )
    upgrade_to_head(settings.database_url or "")

    user_a, ws_a = uuid4(), uuid4()
    user_b, ws_b = uuid4(), uuid4()
    tip = _git_commit()
    evidence: dict[str, object] = {
        "gate": "L2",
        "entrypoint": "create_live_app",
        "tip_commit": tip,
        "uv_lock_sha256_16": _lockfile_fingerprint(),
        "workspace_a": str(ws_a),
        "workspace_b": str(ws_b),
    }

    app = create_live_app(settings=settings)

    with TestClient(app) as client:
        ha, hb = _hdr(user_a, ws_a), _hdr(user_b, ws_b)

        sess_a = client.post("/api/sessions", headers=ha, json={"title": "ws-a"})
        assert sess_a.status_code == 201, sess_a.text
        session_a = UUID(sess_a.json()["id"])

        run_a = client.portal.call(
            functools.partial(
                app.state.session_repository.begin_run,
                session_a,
                lease_owner="l2-a",
            )
        )

        up_a = client.post(
            "/api/files",
            headers=ha,
            files={"file": ("a.txt", b"secret-a", "text/plain")},
        )
        assert up_a.status_code in {200, 201}, up_a.text
        attachment_a = UUID(up_a.json()["id"])

        artifact_a = uuid4()
        artifact_bytes = b"artifact-secret-a"
        artifact_checksum = hashlib.sha256(artifact_bytes).hexdigest()
        volume_paths = volume_paths_from_settings(settings)
        durable_path = str(volume_paths.artifact_blob_path(artifact_a))

        async def _commit_artifact_a() -> None:
            await app.state.workspace_volume_gateway.write_bytes(ws_a, durable_path, artifact_bytes)
            await app.state.session_repository.commit_completed_turn(
                session_a,
                user_text="create isolated artifact",
                assistant_text="done",
                run_id=run_a,
                expected_checkpoint_version=0,
                artifact_candidates=(
                    ArtifactCandidate(
                        id=artifact_a,
                        user_id=user_a,
                        workspace_id=ws_a,
                        session_id=session_a,
                        run_id=run_a,
                        kind="text",
                        title="a-art",
                        media_type="text/plain",
                        byte_size=len(artifact_bytes),
                        checksum_sha256=artifact_checksum,
                        staging_path="private-test-candidate",
                        durable_path=durable_path,
                    ),
                ),
            )

        client.portal.call(_commit_artifact_a)
        assert client.get(f"/api/artifacts/{artifact_a}", headers=ha).status_code == 200

        r_sess = client.get(f"/api/sessions/{session_a}", headers=hb)
        r_att = client.get(f"/api/files/{attachment_a}", headers=hb)
        r_art = client.get(f"/api/artifacts/{artifact_a}", headers=hb)
        r_cancel = client.post(f"/api/runs/{run_a}/cancel", headers=hb)

        evidence["b_session_status"] = r_sess.status_code
        evidence["b_attachment_status"] = r_att.status_code
        evidence["b_artifact_status"] = r_art.status_code
        evidence["b_cancel_status"] = r_cancel.status_code

        assert r_sess.status_code == 404
        assert r_att.status_code == 404
        assert r_art.status_code == 404
        assert r_cancel.status_code == 404

        resources = app.state.live_kernel_resources
        session_b = UUID(client.post("/api/sessions", headers=hb, json={"title": "ws-b"}).json()["id"])

        async def _volume_isolation() -> dict[str, object]:
            lease_a = await resources.session_manager.acquire(
                LeaseRequest(session_id=session_a, user_id=user_a, workspace_id=ws_a)
            )
            lease_b = await resources.session_manager.acquire(
                LeaseRequest(session_id=session_b, user_id=user_b, workspace_id=ws_b)
            )
            resources.track_sandbox(lease_a.sandbox_id)
            resources.track_sandbox(lease_b.sandbox_id)
            a_sub = workspace_volume_subpath(ws_a)
            b_sub = workspace_volume_subpath(ws_b)
            assert lease_a.volume_subpath == a_sub
            assert lease_b.volume_subpath == b_sub
            assert lease_b.volume_subpath != a_sub
            await resources.session_manager.release(lease_a)
            await resources.session_manager.release(lease_b)
            return {
                "b_volume_subpath": lease_b.volume_subpath,
                "a_volume_subpath": a_sub,
                "mounts_differ": lease_b.volume_subpath != a_sub,
            }

        vol = client.portal.call(_volume_isolation)
        evidence.update(vol)
        assert vol["mounts_differ"] is True
        evidence["result"] = "PASS"

        _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        _EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert _EVIDENCE.is_file()
        assert evidence["tip_commit"] == tip
