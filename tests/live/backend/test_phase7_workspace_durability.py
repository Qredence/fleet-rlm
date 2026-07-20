"""Opt-in live proof for Session Workspace durability across Sandbox replacement."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import SandboxBinding
from fleet_rlm.daytona.paths import volume_paths_from_settings
from fleet_rlm.daytona.run_environment import LiveKernelResources
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.daytona.workspace_fs import DaytonaSessionWorkspaceFS

pytestmark = [pytest.mark.live_daytona]


def _skip_unless_live(settings: Settings) -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for live Phase 7 durability tests")
    if settings.daytona_api_key is None:
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")
    if not settings.daytona_snapshot:
        pytest.skip("FLEET_DAYTONA_SNAPSHOT not configured")


def _assert_complete_layout(sandbox: object, *, mount: str, session_id: object, run_id: object) -> None:
    session = f"{mount}/sessions/{session_id}"
    run = f"{session}/runs/{run_id}"
    required = (
        f"{mount}/skills",
        f"{mount}/memory",
        f"{mount}/artifacts",
        f"{mount}/attachments",
        f"{mount}/sessions",
        session,
        f"{session}/exports",
        f"{session}/staging",
        f"{session}/workspace",
        f"{session}/runs",
        run,
        f"{run}/staging",
        f"{run}/artifacts",
        f"{run}/attachments",
    )
    filesystem = getattr(sandbox, "fs")
    for path in required:
        info = filesystem.get_file_info(path)
        assert info.is_dir, path


def _link_diagnostic(sandbox: object, *, volume_root: str, workspace_root: str) -> dict[str, object]:
    code = "\n".join(
        (
            "import errno, json, os",
            f"root = {workspace_root!r}",
            "parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)",
            "temporary = '.fleet-link-diagnostic'",
            f"result = {{'volume_root': {volume_root!r}, 'workspace_root': root}}",
            "try:",
            "    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)",
            "    os.write(fd, b'probe')",
            "    os.fsync(fd)",
            "    os.close(fd)",
            "    try:",
            "        os.link(temporary, '.fleet-link-diagnostic-target', src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)",
            "        result['link'] = {'ok': True}",
            "    except OSError as exc:",
            "        result['link'] = {'ok': False, 'errno': exc.errno, 'name': errno.errorcode.get(exc.errno)}",
            "finally:",
            "    for name in (temporary, '.fleet-link-diagnostic-target'):",
            "        try:",
            "            os.unlink(name, dir_fd=parent_fd)",
            "        except FileNotFoundError:",
            "            pass",
            "    os.close(parent_fd)",
            "print(json.dumps(result))",
        )
    )
    response = getattr(sandbox, "process").code_run(code)
    return json.loads(str(getattr(response, "result", "")))


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_daytona_workspace_link_errno_diagnostic() -> None:
    settings = Settings(run_environment="daytona")
    _skip_unless_live(settings)
    resources = LiveKernelResources(settings)
    lease = None
    session_id = uuid4()
    try:
        lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=uuid4(),
                workspace_id=uuid4(),
                run_id=uuid4(),
            ),
            deadline=asyncio.get_running_loop().time() + 600,
        )
        resources.track_sandbox(lease.sandbox_id)
        sandbox = resources.platform.get(lease.sandbox_id)
        assert sandbox is not None
        paths = volume_paths_from_settings(settings)
        diagnostic = _link_diagnostic(
            sandbox,
            volume_root=str(paths.mount_path),
            workspace_root=str(paths.session_workspace_dir(session_id)),
        )
        print(json.dumps({key: diagnostic.get(key) for key in ("volume_root", "workspace_root", "link")}))
        assert diagnostic["link"] == {"ok": False, "errno": 1, "name": "EPERM"}
    finally:
        if lease is not None:
            await resources.session_manager.release(lease)
        resources.cleanup()
        await resources.adispose_engine()


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_session_workspace_survives_sandbox_replacement() -> None:
    settings = Settings(run_environment="daytona")
    _skip_unless_live(settings)
    user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
    first_run_id, second_run_id = uuid4(), uuid4()
    resources = LiveKernelResources(settings)
    first_lease = None
    second_lease = None

    try:
        first_lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
                run_id=first_run_id,
            ),
            deadline=asyncio.get_running_loop().time() + 600,
        )
        resources.track_sandbox(first_lease.sandbox_id)
        first_sandbox = resources.platform.get(first_lease.sandbox_id)
        assert first_sandbox is not None
        assert getattr(first_sandbox, "snapshot", None) == settings.daytona_snapshot
        paths = volume_paths_from_settings(settings)
        _assert_complete_layout(
            first_sandbox,
            mount=str(paths.mount_path),
            session_id=session_id,
            run_id=first_run_id,
        )
        first_workspace = DaytonaSessionWorkspaceFS(
            first_sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(session_id)),
            max_file_bytes=settings.max_upload_bytes,
        )
        written = first_workspace.write_text(
            "notes/decision.md",
            "durable across replacement",
            overwrite=False,
        )
        assert written.byte_size == len(b"durable across replacement")
        today = datetime.now(UTC).date().isoformat()
        first_workspace.write_text("date.txt", today, overwrite=True)
        assert first_workspace.read_text("date.txt", max_bytes=100) == today
        first_workspace.write_text("date.txt", "verified", overwrite=True)
        assert first_workspace.read_text("date.txt", max_bytes=100) == "verified"
        await resources.session_manager.release(first_lease)
        first_lease = None

        binding = await resources.bindings.get(session_id)
        assert binding is not None
        old_sandbox_id = binding.sandbox_id
        replacement = await resources.session_manager.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=old_sandbox_id,
                workspace_id=workspace_id,
                volume_id=binding.volume_id,
                volume_subpath=binding.volume_subpath,
                mount_path=binding.mount_path,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
            user_id=user_id,
        )
        assert replacement.sandbox_id is not None
        assert replacement.sandbox_id != old_sandbox_id
        resources.track_sandbox(replacement.sandbox_id)
        replacement_sandbox = resources.platform.get(replacement.sandbox_id)
        assert replacement_sandbox is not None
        assert getattr(replacement_sandbox, "snapshot", None) == settings.daytona_snapshot

        second_lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
                run_id=second_run_id,
            ),
            deadline=asyncio.get_running_loop().time() + 600,
        )
        resources.track_sandbox(second_lease.sandbox_id)
        second_sandbox = resources.platform.get(second_lease.sandbox_id)
        assert second_sandbox is not None
        assert getattr(second_sandbox, "snapshot", None) == settings.daytona_snapshot
        _assert_complete_layout(
            second_sandbox,
            mount=str(paths.mount_path),
            session_id=session_id,
            run_id=second_run_id,
        )
        second_workspace = DaytonaSessionWorkspaceFS(
            second_sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(session_id)),
            max_file_bytes=settings.max_upload_bytes,
        )

        assert second_workspace.read_text("notes/decision.md", max_bytes=1024) == ("durable across replacement")
        assert second_workspace.read_text("date.txt", max_bytes=100) == "verified"
        final_listing = second_workspace.list_entries(".")
        assert {entry.path for entry in final_listing.entries} == {"date.txt", "notes"}
        entry = second_workspace.stat("notes/decision.md")
        assert entry is not None
        assert entry.byte_size == written.byte_size
        assert entry.modified_at is not None
    finally:
        if first_lease is not None:
            await resources.session_manager.release(first_lease)
        if second_lease is not None:
            await resources.session_manager.release(second_lease)
        resources.cleanup()
        await resources.adispose_engine()
