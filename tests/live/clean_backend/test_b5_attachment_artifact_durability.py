"""Opt-in live B5 proof: staged Attachment + Artifact under Workspace Volume Scope.

Gate: FLEET_CLEAN_LIVE=1

(1) Stage Attachment into Volume scope; readable from Sandbox during the Run.
(2) Artifact durable blob survives Sandbox replace with remounted same scope;
    retrieve by id with matching checksum.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.chat.live_context import LiveKernelResources
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.bindings import SandboxBinding
from fleet_rlm_clean.daytona.session_manager import LeaseRequest
from fleet_rlm_clean.daytona.volume_fs import DaytonaSandboxVolumeFs
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.uploads import LocalAttachmentStore

pytestmark = [pytest.mark.live_daytona]

ATTACHMENT_BYTES = b"b5-staged-attachment-payload"
ARTIFACT_TEXT = "b5-durable-artifact-body"


def _live_enabled() -> bool:
    return os.environ.get("FLEET_CLEAN_LIVE", "").strip() in {"1", "true", "yes"} or os.environ.get(
        "FLEET_RLM_RUN_LIVE_LLM_TESTS", ""
    ).strip() in {"1", "true", "yes"}


def _have_daytona() -> bool:
    return bool(os.environ.get("DAYTONA_API_KEY") or os.environ.get("FLEET_CLEAN_DAYTONA_API_KEY"))


def _skip_unless_live() -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_CLEAN_LIVE=1 for live B5 durability tests")
    if not _have_daytona():
        pytest.skip("DAYTONA_API_KEY / FLEET_CLEAN_DAYTONA_API_KEY not configured")


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


def _write_evidence(name: str, payload: dict[str, Any]) -> Path:
    evidence_dir = Path(".scratch/clean-backend-refoundation/assets")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_live_b5_stage_readable_and_artifact_survives_replace(tmp_path: Path) -> None:
    _skip_unless_live()

    user_id, workspace_id = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    resources = LiveKernelResources(Settings(), allow_ephemeral_fallback=False)
    sandbox_ids: list[str] = []
    volume_id: str | None = None

    try:
        lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease.sandbox_id)
        sandbox_ids.append(lease.sandbox_id)
        volume_id = lease.volume_id
        assert lease.volume_subpath == f"workspaces/{workspace_id}"

        sandbox = resources.platform.get(lease.sandbox_id)
        assert sandbox is not None
        volume_fs = DaytonaSandboxVolumeFs(sandbox)

        attachment_store = LocalAttachmentStore(
            tmp_path / "attachments",
            max_bytes=1024 * 1024,
            volume_fs=volume_fs,
        )
        artifact_store = LocalArtifactStore(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_fs=volume_fs,
        )
        ref = attachment_store.upload(
            user_id=user_id,
            workspace_id=workspace_id,
            filename="b5.txt",
            content_type="text/plain",
            data=ATTACHMENT_BYTES,
        )
        stager = AttachmentStager(attachment_store, volume_fs=volume_fs)
        staged = stager.stage(
            ref.id,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
        )

        lease.interpreter.start()
        read_staged = lease.interpreter.execute(
            "from pathlib import Path\n"
            f"p = Path({staged.sandbox_path!r})\n"
            "print(p.read_text(encoding='utf-8') if p.is_file() else 'MISSING')\n"
        )
        assert ATTACHMENT_BYTES.decode() in read_staged

        art = artifact_store.create(
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            run_id=run_id,
            kind="text",
            content=ARTIFACT_TEXT,
            title="b5",
        )
        durable = artifact_store.durable_volume_blob_path(art.id, user_id=user_id, workspace_id=workspace_id)
        await resources.session_manager.release(lease)

        binding = await resources.bindings.get(session_id)
        assert binding is not None
        old_sid = binding.sandbox_id
        new_binding = await resources.session_manager.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=old_sid,
                workspace_id=workspace_id,
                volume_id=volume_id or "",
                volume_subpath=f"workspaces/{workspace_id}",
                mount_path=lease.mount_path,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
        )
        assert new_binding.sandbox_id != old_sid
        resources.track_sandbox(new_binding.sandbox_id)
        if new_binding.sandbox_id:
            sandbox_ids.append(new_binding.sandbox_id)

        lease2 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        )
        resources.track_sandbox(lease2.sandbox_id)
        if lease2.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease2.sandbox_id)

        sandbox2 = resources.platform.get(lease2.sandbox_id)
        assert sandbox2 is not None
        volume_fs2 = DaytonaSandboxVolumeFs(sandbox2)
        remounted = volume_fs2.read_bytes(durable)
        assert remounted == ARTIFACT_TEXT.encode("utf-8")
        assert hashlib.sha256(remounted).hexdigest() == art.checksum_sha256

        # Catalog re-read after replace still resolves via Volume Scope when rebound.
        rebound_store = LocalArtifactStore(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_fs=volume_fs2,
        )
        assert rebound_store.read_bytes(art.id, user_id=user_id, workspace_id=workspace_id) == (
            ARTIFACT_TEXT.encode("utf-8")
        )

        await resources.session_manager.release(lease2)

        evidence = {
            "gate": "B5",
            "git_commit": _git_commit(),
            "uv_lock_fingerprint": _lockfile_fingerprint(),
            "workspace_id": str(workspace_id),
            "volume_id": volume_id,
            "volume_subpath": f"workspaces/{workspace_id}",
            "staged_path_prefix": "/home/daytona/fleet/sessions/",
            "staged_readable": True,
            "artifact_id": str(art.id),
            "artifact_checksum": art.checksum_sha256,
            "artifact_survived_replace": True,
            "sandbox_ids": sandbox_ids,
        }
        path = _write_evidence("live-b5-attachment-artifact-durability-evidence.json", evidence)
        assert path.is_file()
    finally:
        resources.cleanup()
        await resources.adispose_engine()
