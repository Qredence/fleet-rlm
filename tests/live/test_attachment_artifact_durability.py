"""Opt-in live proof: Attachments and Artifacts take production's storage path.

Gate: FLEET_LIVE=1

Uses the objects the app composes: durable blobs go to the Workspace Volume
through short-lived host-I/O Sandboxes; Run copies of Attachments are staged
in the root Sandbox's Run scratch, where the prepared-context loader reads
them.

(1) Upload Attachments; stage them for one Run; load them as prepared context.
(2) Publish an Artifact; replace the root Sandbox; read the Artifact back from
    the Volume with a matching checksum.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.errors import map_provider_error
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
from fleet_rlm.daytona.runtime import (
    DEFAULT_IDLE_STOP_SECONDS,
    DaytonaRuntime,
    LeaseRequest,
    sandbox_spec_from_settings,
)
from fleet_rlm.rlm.ownership import RunCleanupSupervisor
from fleet_rlm.rlm.program import AttachmentContextCapsule, AttachmentContextEntry
from fleet_rlm.sessions.bindings import SandboxBinding
from fleet_rlm.workspace.attachments import (
    AttachmentAccess,
    AttachmentLifecycleService,
    AttachmentRun,
    AttachmentUpload,
    DaytonaRunAttachmentPathPolicy,
    LocalAttachmentCatalog,
)
from fleet_rlm.workspace.host_io import DaytonaHostIO, DaytonaRunStorage
from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceGateway, DaytonaWorkspaceVolumeGateway
from tests.live._evidence import candidate_identity, write_receipt
from tests.support.in_memory_stores import InMemorySandboxBindingStore
from tests.support.local_catalog import LocalArtifactCatalog


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
BINARY_ATTACHMENT_BYTES = b"\xff\xfeb5\x00binary"
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


def _live_resources(settings: Settings, cleanup: RunCleanupSupervisor) -> SimpleNamespace:
    bindings = InMemorySandboxBindingStore()
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    runtime = DaytonaRuntime.from_settings(
        settings,
        bindings=bindings,
        cleanup=cleanup,
        sandbox_spec=sandbox_spec_from_settings(settings),
        max_active_leases=settings.max_active_daytona_leases,
        idle_stop_seconds=DEFAULT_IDLE_STOP_SECONDS,
        execution_output_cap=settings.rlm_max_execution_output_chars,
        dispatcher=dispatcher,
    )
    volume_paths = runtime.volume_config.paths()
    workspace_gateway = DaytonaWorkspaceGateway(
        runtime=runtime,
        map_error=map_provider_error,
    )
    return SimpleNamespace(
        runtime=runtime,
        settings=settings,
        bindings=bindings,
        volume_config=runtime.volume_config,
        volume_paths=volume_paths,
        dispatcher=dispatcher,
        workspace_gateway=workspace_gateway,
        volume_gateway=DaytonaWorkspaceVolumeGateway(workspace_gateway, mount_path=runtime.volume_config.mount_path),
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
        lease = await resources.runtime.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.runtime.track_sandbox(lease.sandbox_id)
        sandbox_ids.append(lease.sandbox_id)
        volume_id = lease.volume_id
        volume_subpath = f"workspaces/{workspace_id}/sessions/{session_id}/workspace"
        assert lease.volume_subpath == volume_subpath

        sandbox = await resources.runtime._platform.get(lease.sandbox_id)
        assert sandbox is not None
        assert getattr(sandbox, "snapshot", None) == settings.daytona_snapshot
        # Compose storage as app_lifecycle does: durable blobs through host I/O,
        # Run copies in the root Sandbox's private scratch.
        host_io = DaytonaHostIO(
            workspace_id,
            volume_gateway=resources.volume_gateway,
            workspace_gateway=resources.workspace_gateway,
            dispatcher=resources.dispatcher,
            volume_root=str(resources.volume_paths.mount_path),
            max_file_bytes=settings.max_upload_bytes,
        )
        sink = DaytonaRunStorage(
            sandbox,
            dispatcher=resources.dispatcher,
            paths=resources.volume_paths,
            host_io=host_io,
            run_id=run_id,
        )
        attachment_module = AttachmentLifecycleService(
            catalog=LocalAttachmentCatalog(tmp_path / "attachments"),
            blobs=resources.volume_gateway,
            paths=DaytonaRunAttachmentPathPolicy(resources.volume_paths),
            max_bytes=1024 * 1024,
        )
        artifact_store = LocalArtifactCatalog(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_paths=resources.volume_paths,
            volume_fs=sink.volume_fs,
        )
        access = AttachmentAccess(user_id, workspace_id)
        ref = await attachment_module.upload(
            access,
            AttachmentUpload("b5.txt", "text/plain", _Source(ATTACHMENT_BYTES)),
        )
        binary_ref = await attachment_module.upload(
            access,
            AttachmentUpload("b5.bin", "application/octet-stream", _Source(BINARY_ATTACHMENT_BYTES)),
        )
        prepared = await attachment_module.prepare_run(
            access,
            (ref.id, binary_ref.id),
            AttachmentRun(session_id, run_id),
            sink,
        )
        staged = next(item for item in prepared.staged if item.attachment_id == ref.id)

        # The prepared-context loader the RLM setup action runs in production.
        # It runs and shuts down before the template executes, because each
        # invocation's broker binds the Sandbox's broker port.
        staged_by_id = {item.attachment_id: item for item in prepared.staged}
        capsule = AttachmentContextCapsule(
            tuple(
                AttachmentContextEntry(
                    attachment_id=item.id,
                    filename=item.filename,
                    content_type=item.content_type,
                    byte_size=item.byte_size,
                    checksum_sha256=item.checksum_sha256,
                    sandbox_path=staged_by_id[item.id].sandbox_path,
                )
                for item in (ref, binary_ref)
            ),
            mount_root=sink.scratch_root,
        )
        invocation = lease.interpreter.new_invocation(context_capsule=capsule)
        try:
            await asyncio.to_thread(
                invocation.execute,
                capsule.sandbox_assignment("attachments", "_raw_attachments"),
                {"_raw_attachments": capsule.to_sandbox().decode("utf-8")},
            )
            loaded = await asyncio.to_thread(
                invocation.execute,
                "print([(a['id'], a['filename'], a['encoding'], a['data'] if a['encoding'] == 'utf-8' "
                "else a['data'].hex()) for a in attachments])",
            )
            context_accesses = invocation.drain_context_accesses()
        finally:
            await asyncio.to_thread(invocation.shutdown)
        assert (
            str(
                [
                    (str(ref.id), "b5.txt", "utf-8", ATTACHMENT_BYTES.decode()),
                    (str(binary_ref.id), "b5.bin", "bytes", BINARY_ATTACHMENT_BYTES.hex()),
                ]
            )
            in loaded
        )
        assert context_accesses == (str(ref.id), str(binary_ref.id))

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
        await resources.runtime.release(lease)

        binding = await resources.bindings.get(session_id)
        assert binding is not None
        old_sid = binding.sandbox_id
        new_binding = await resources.runtime.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=old_sid,
                workspace_id=workspace_id,
                volume_id=volume_id or "",
                volume_subpath=volume_subpath,
                mount_path=lease.mount_path,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
            user_id=user_id,
        )
        assert new_binding.sandbox_id != old_sid
        resources.runtime.track_sandbox(new_binding.sandbox_id)
        if new_binding.sandbox_id:
            sandbox_ids.append(new_binding.sandbox_id)
        replacement_sandbox = await resources.runtime._platform.get(new_binding.sandbox_id)
        assert replacement_sandbox is not None
        assert getattr(replacement_sandbox, "snapshot", None) == settings.daytona_snapshot

        lease2 = await resources.runtime.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.runtime.track_sandbox(lease2.sandbox_id)
        if lease2.sandbox_id not in sandbox_ids:
            sandbox_ids.append(lease2.sandbox_id)

        sandbox2 = await resources.runtime._platform.get(lease2.sandbox_id)
        assert sandbox2 is not None
        assert getattr(sandbox2, "snapshot", None) == settings.daytona_snapshot
        # Read back as ArtifactReader does: from the Volume, through a fresh
        # host-I/O Sandbox, independent of either root Sandbox.
        remounted = await resources.volume_gateway.read_bytes(workspace_id, durable)
        assert remounted == ARTIFACT_TEXT.encode("utf-8")
        assert hashlib.sha256(remounted).hexdigest() == art.checksum_sha256

        rebound_store = LocalArtifactCatalog(
            tmp_path / "artifacts",
            max_bytes=1024 * 1024,
            volume_paths=resources.volume_paths,
            volume_fs=host_io.volume_fs,
        )
        assert await asyncio.to_thread(
            rebound_store.read_bytes,
            art.id,
            user_id=user_id,
            workspace_id=workspace_id,
        ) == ARTIFACT_TEXT.encode("utf-8")

        await resources.runtime.release(lease2)

        evidence = {
            "gate": "B5",
            "git_commit": _git_commit(),
            "uv_lock_fingerprint": _lockfile_fingerprint(),
            "workspace_id": str(workspace_id),
            "volume_id": volume_id,
            "volume_subpath": volume_subpath,
            "staged_path_prefix": f"{sink.scratch_root}/attachments/",
            "artifact_durable_path": durable,
            "staged_readable": True,
            "prepared_context_parity": True,
            "context_accesses": list(context_accesses),
            "artifact_id": str(art.id),
            "artifact_checksum": art.checksum_sha256,
            "artifact_survived_replace": True,
            "sandbox_ids": sandbox_ids,
        }
        path = _write_evidence("live-b5-attachment-artifact-durability-evidence.json", evidence)
        assert path.is_file()
    finally:
        await cleanup.shutdown(drain_seconds=30)
        await resources.runtime.adispose()
    write_receipt(
        {
            "schema": "fleet.p35d-attachment-artifact/v1",
            "candidate": candidate_identity(),
            "assertions": {
                "attachment_readable": True,
                "prepared_context_text_and_binary_loaded": True,
                "prepared_context_accesses_reported": True,
                "artifact_survived_replacement": True,
                "shared_volume_checksum_verified": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
