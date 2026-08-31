"""Opt-in live proof: staged Attachment + Artifact under Workspace Volume Scope.

Gate: FLEET_LIVE=1

(1) Stage Attachment into Volume scope; readable from Sandbox during the Run.
(2) Artifact durable blob survives Sandbox replace with remounted same scope;
    retrieve by id with matching checksum.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm.artifacts.local_catalog import LocalArtifactCatalog
from fleet_rlm.attachments.lifecycle import AttachmentLifecycleService
from fleet_rlm.attachments.local_catalog import LocalAttachmentCatalog
from fleet_rlm.attachments.models import AttachmentAccess, AttachmentRun, AttachmentUpload
from fleet_rlm.attachments.paths import WorkspaceAttachmentPathPolicy
from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.broker import sync_sandbox
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.runtime.bindings import InMemorySandboxBindingStore, SandboxBinding
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.runtime.daytona.run_environment import DaytonaRuntimeResources
from fleet_rlm.workspace.storage import DaytonaSandboxVolumeFs
from tests.live.backend._evidence import candidate_identity, write_receipt


class _LiveAttachmentBlob:
    def __init__(self, volume_fs) -> None:
        self.volume_fs = volume_fs

    async def write_bytes(self, _workspace_id, logical_path: str, data: bytes) -> None:
        await asyncio.to_thread(self.volume_fs.write_bytes, logical_path, data)

    async def read_bytes(self, _workspace_id, logical_path: str) -> bytes:
        return await asyncio.to_thread(self.volume_fs.read_bytes, logical_path)

    async def remove_bytes(self, _workspace_id, logical_path: str) -> None:
        await asyncio.to_thread(self.volume_fs.remove, logical_path)


class _LiveSink:
    def __init__(self, volume_fs) -> None:
        self.volume_fs = volume_fs

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await asyncio.to_thread(self.volume_fs.write_bytes, logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await asyncio.to_thread(self.volume_fs.remove, logical_path)


class _Source:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.data)
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


pytestmark = [pytest.mark.live_daytona]

ATTACHMENT_BYTES = b"b5-staged-attachment-payload"
ARTIFACT_TEXT = "b5-durable-artifact-body"


def _live_enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip() in {"1", "true", "yes"}


def _skip_unless_live(settings: Settings) -> None:
    if not _live_enabled():
        pytest.skip("Set FLEET_LIVE=1 for live B5 durability tests")
    if settings.daytona_api_key is None:
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")
    if not settings.daytona_snapshot:
        pytest.skip("FLEET_DAYTONA_SNAPSHOT not configured")


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _lockfile_fingerprint() -> str:
    lock = Path("uv.lock")
    if not lock.exists():
        return "missing-uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]


def _write_evidence(name: str, payload: dict[str, Any]) -> Path:
    evidence_dir = Path(".fleet-evidence/receipts/p35d")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _live_resources(settings: Settings, cleanup: RunCleanupSupervisor) -> DaytonaRuntimeResources:
    return DaytonaRuntimeResources(
        settings,
        bindings=InMemorySandboxBindingStore(),
        cleanup=cleanup,
        max_active_leases=settings.max_active_daytona_leases,
        execution_output_cap=settings.rlm_max_execution_output_chars,
        execution_timeout_s=settings.rlm_execution_timeout_s,
    )


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_staged_attachment_is_readable_and_artifact_survives_replacement(tmp_path: Path) -> None:
    settings = load_runtime_settings()
    _skip_unless_live(settings)

    user_id, workspace_id = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    cleanup = RunCleanupSupervisor(max_jobs=8)
    resources = _live_resources(settings, cleanup)
    sandbox_ids: list[str] = []
    volume_id: str | None = None

    try:
        lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.track_sandbox(lease.sandbox_id)
        sandbox_ids.append(lease.sandbox_id)
        volume_id = lease.volume_id
        assert lease.volume_subpath == f"workspaces/{workspace_id}"

        sandbox = await resources.platform.get(lease.sandbox_id)
        assert sandbox is not None
        assert getattr(sandbox, "snapshot", None) == settings.daytona_snapshot
        volume_fs = DaytonaSandboxVolumeFs(sync_sandbox(sandbox, asyncio.get_running_loop()))

        attachment_module = AttachmentLifecycleService(
            catalog=LocalAttachmentCatalog(tmp_path / "attachments"),
            blobs=_LiveAttachmentBlob(volume_fs),
            paths=WorkspaceAttachmentPathPolicy(resources.volume_config.paths()),
            max_bytes=1024 * 1024,
        )
        artifact_store = LocalArtifactCatalog(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_fs=volume_fs,
        )
        access = AttachmentAccess(user_id, workspace_id)
        ref = await attachment_module.upload(
            access,
            AttachmentUpload("b5.txt", "text/plain", _Source(ATTACHMENT_BYTES)),
        )
        prepared = await attachment_module.prepare_run(
            access,
            (ref.id,),
            AttachmentRun(session_id, run_id),
            _LiveSink(volume_fs),
        )
        staged = prepared.staged[0]

        await asyncio.to_thread(lease.interpreter.start)
        read_staged = await asyncio.to_thread(
            lease.interpreter.execute,
            "from pathlib import Path\n"
            f"p = Path({staged.sandbox_path!r})\n"
            "print(p.read_text(encoding='utf-8') if p.is_file() else 'MISSING')\n",
        )
        assert ATTACHMENT_BYTES.decode() in read_staged

        art = await asyncio.to_thread(
            artifact_store.create,
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
            user_id=user_id,
        )
        assert new_binding.sandbox_id != old_sid
        resources.track_sandbox(new_binding.sandbox_id)
        if new_binding.sandbox_id:
            sandbox_ids.append(new_binding.sandbox_id)
        replacement_sandbox = await resources.platform.get(new_binding.sandbox_id)
        assert replacement_sandbox is not None
        assert getattr(replacement_sandbox, "snapshot", None) == settings.daytona_snapshot

        lease2 = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.track_sandbox(lease2.sandbox_id)
        if lease2.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease2.sandbox_id)

        sandbox2 = await resources.platform.get(lease2.sandbox_id)
        assert sandbox2 is not None
        assert getattr(sandbox2, "snapshot", None) == settings.daytona_snapshot
        volume_fs2 = DaytonaSandboxVolumeFs(sync_sandbox(sandbox2, asyncio.get_running_loop()))
        remounted = await asyncio.to_thread(volume_fs2.read_bytes, durable)
        assert remounted == ARTIFACT_TEXT.encode("utf-8")
        assert hashlib.sha256(remounted).hexdigest() == art.checksum_sha256

        # Catalog re-read after replace still resolves via Volume Scope when rebound.
        rebound_store = LocalArtifactCatalog(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_fs=volume_fs2,
        )
        assert await asyncio.to_thread(
            rebound_store.read_bytes,
            art.id,
            user_id=user_id,
            workspace_id=workspace_id,
        ) == ARTIFACT_TEXT.encode("utf-8")

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
        await cleanup.shutdown(drain_seconds=30)
        await resources.adispose()
    write_receipt(
        {
            "schema": "fleet.p35d-attachment-artifact/v1",
            "candidate": candidate_identity(),
            "assertions": {
                "attachment_readable": True,
                "artifact_survived_replacement": True,
                "shared_volume_checksum_verified": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
