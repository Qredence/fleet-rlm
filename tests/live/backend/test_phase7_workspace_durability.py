"""Opt-in live proof for Session Workspace durability across Sandbox replacement."""

from __future__ import annotations

import asyncio
import os
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


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_session_workspace_survives_sandbox_replacement() -> None:
    settings = Settings(run_environment="daytona")
    _skip_unless_live(settings)
    user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
    resources = LiveKernelResources(settings)
    first_lease = None
    second_lease = None

    try:
        first_lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 600,
        )
        resources.track_sandbox(first_lease.sandbox_id)
        first_sandbox = resources.platform.get(first_lease.sandbox_id)
        assert first_sandbox is not None
        paths = volume_paths_from_settings(settings)
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

        second_lease = await resources.session_manager.acquire(
            LeaseRequest(
                session_id=session_id,
                user_id=user_id,
                workspace_id=workspace_id,
            ),
            deadline=asyncio.get_running_loop().time() + 600,
        )
        resources.track_sandbox(second_lease.sandbox_id)
        second_sandbox = resources.platform.get(second_lease.sandbox_id)
        assert second_sandbox is not None
        second_workspace = DaytonaSessionWorkspaceFS(
            second_sandbox,
            volume_root=str(paths.mount_path),
            root=str(paths.session_workspace_dir(session_id)),
            max_file_bytes=settings.max_upload_bytes,
        )

        assert second_workspace.read_text("notes/decision.md", max_bytes=1024) == ("durable across replacement")
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
