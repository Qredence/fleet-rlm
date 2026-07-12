"""Exit-bar L2 — tip-matched adversarial workspace isolation.

Gate: FLEET_CLEAN_LIVE=1
Workspace B must not access Workspace A session/attachment/artifact/run;
Volume mounts must use distinct Workspace Volume Scope subpaths.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fleet_rlm_clean.app import create_live_app
from fleet_rlm_clean.chat.live_context import settings_with_env_fallbacks
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.session_manager import LeaseRequest
from fleet_rlm_clean.daytona.volumes import workspace_volume_subpath

pytestmark = [pytest.mark.live_daytona]

_EVIDENCE = Path(".scratch/clean-backend-refoundation/assets/live-adversarial-isolation-evidence.json")


def _live_enabled() -> bool:
    return os.environ.get("FLEET_CLEAN_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY"))


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_CLEAN_LIVE=1 for exit-bar L2")
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


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@pytest.mark.timeout(600)
def test_exit_bar_l2_adversarial_isolation(tmp_path: Path) -> None:
    _skip_unless_live()

    db_path = tmp_path / "l2.sqlite"
    upload_root = tmp_path / "uploads"
    artifact_root = tmp_path / "artifacts"
    upload_root.mkdir()
    artifact_root.mkdir()

    base = settings_with_env_fallbacks(Settings())
    daytona_key = (
        base.daytona_api_key.get_secret_value()
        if base.daytona_api_key is not None
        else os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY") or ""
    )
    llm_key = (
        base.llm_api_key.get_secret_value()
        if base.llm_api_key is not None
        else (
            os.environ.get("FLEET_CLEAN_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DSPY_LLM_API_KEY")
            or "unused-for-l2"
        )
    )
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

        run_a = _run_async(app.state.session_repository.begin_run(session_a, lease_owner="l2-a"))

        up_a = client.post(
            "/api/files",
            headers=ha,
            files={"file": ("a.txt", b"secret-a", "text/plain")},
        )
        assert up_a.status_code in {200, 201}, up_a.text
        attachment_a = UUID(up_a.json()["id"])

        art_a = client.post(
            "/api/artifacts",
            headers=ha,
            json={
                "session_id": str(session_a),
                "run_id": str(run_a),
                "kind": "text",
                "title": "a-art",
                "content": "artifact-secret-a",
            },
        )
        assert art_a.status_code in {200, 201}, art_a.text
        artifact_a = UUID(art_a.json()["id"])

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
            lease_b = await resources.session_manager.acquire(
                LeaseRequest(session_id=session_b, user_id=user_b, workspace_id=ws_b)
            )
            resources.track_sandbox(lease_b.sandbox_id)
            a_sub = workspace_volume_subpath(ws_a)
            b_sub = workspace_volume_subpath(ws_b)
            assert lease_b.volume_subpath == b_sub
            assert lease_b.volume_subpath != a_sub
            await resources.session_manager.release(lease_b)
            return {
                "b_volume_subpath": lease_b.volume_subpath,
                "a_volume_subpath": a_sub,
                "mounts_differ": lease_b.volume_subpath != a_sub,
            }

        vol = _run_async(_volume_isolation())
        evidence.update(vol)
        assert vol["mounts_differ"] is True
        evidence["result"] = "PASS"

        _EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        _EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert _EVIDENCE.is_file()
        assert evidence["tip_commit"] == tip
