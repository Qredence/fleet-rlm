"""Opt-in live proof that Daytona Session Workspace URL caches survive replacement."""

from __future__ import annotations

import asyncio
import os
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from fleet_rlm.config.loader import load_runtime_settings
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.broker import sync_sandbox
from fleet_rlm.daytona.session_manager import LeaseRequest
from fleet_rlm.observability.tracing import turn_trace
from fleet_rlm.rlm.events import ToolCompleted, observe_tool
from fleet_rlm.runtime.bindings import InMemorySandboxBindingStore, SandboxBinding
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.runtime.daytona.run_environment import DaytonaRuntimeResources
from fleet_rlm.workspace.paths import volume_paths_from_settings
from fleet_rlm.workspace.storage import DaytonaSessionWorkspaceFS
from fleet_rlm.workspace.url import UrlFetchResult, UrlToolHost, WorkspaceUrlSourceStore

pytestmark = [pytest.mark.live_daytona]

_URL = "https://example.com/fleet-live-cache"
_BODY = "live-daytona-url-cache-body"


@dataclass
class _Fetcher:
    calls: list[str]

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        """
        Fetch a fixed response body for the requested URL.

        Parameters:
            url (str): URL to record and fetch.
            max_bytes (int): Maximum response size accepted by the caller.

        Returns:
            UrlFetchResult: The requested URL with a plain-text response body.
        """
        assert max_bytes >= len(_BODY.encode())
        self.calls.append(url)
        return UrlFetchResult(url, "text/plain; charset=utf-8", _BODY)


def _skip_unless_live(settings: Settings) -> None:
    """
    Skip the test unless live Daytona testing is fully configured.

    Parameters:
        settings (Settings): Runtime settings used to determine whether a Daytona snapshot is configured.
    """
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for live Daytona URL cache tests")
    if not os.environ.get("FLEET_DAYTONA_API_KEY"):
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")
    if not settings.daytona_snapshot:
        pytest.skip("FLEET_DAYTONA_SNAPSHOT not configured")


def _workspace(
    sandbox: object,
    settings: Settings,
    session_id: UUID,
    loop: asyncio.AbstractEventLoop,
) -> DaytonaSessionWorkspaceFS:
    """
    Build a Daytona session workspace filesystem for the specified session.

    Parameters:
        sandbox (object): The Daytona sandbox to synchronize with the workspace.
        settings (Settings): Runtime settings used to configure workspace paths and upload limits.
        session_id (UUID): Identifier of the session whose workspace should be used.
        loop (asyncio.AbstractEventLoop): Event loop used to synchronize the sandbox.

    Returns:
        DaytonaSessionWorkspaceFS: The configured session workspace filesystem.
    """
    paths = volume_paths_from_settings(settings)
    return DaytonaSessionWorkspaceFS(
        sync_sandbox(sandbox, loop),
        volume_root=str(paths.mount_path),
        root=str(paths.session_workspace_dir(session_id)),
        max_file_bytes=settings.max_upload_bytes,
    )


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
async def test_url_cache_survives_daytona_sandbox_replacement_with_body_free_events() -> None:
    settings = load_runtime_settings()
    _skip_unless_live(settings)

    user_id, workspace_id, session_id = uuid4(), uuid4(), uuid4()
    resources: DaytonaRuntimeResources | None = None
    cleanup: RunCleanupSupervisor | None = None
    first_lease = None
    second_lease = None
    observed: list[object] = []
    fetcher = _Fetcher([])

    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            cleanup = RunCleanupSupervisor(max_jobs=8)
            resources = _live_resources(settings, cleanup)

        first_lease = await resources.session_manager.acquire(
            LeaseRequest(session_id=session_id, user_id=user_id, workspace_id=workspace_id),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.track_sandbox(first_lease.sandbox_id)
        first_sandbox = await resources.platform.get(first_lease.sandbox_id)
        assert first_sandbox is not None

        first_host = UrlToolHost(
            session_id=session_id,
            store=WorkspaceUrlSourceStore(_workspace(first_sandbox, settings, session_id, asyncio.get_running_loop())),
            max_bytes=settings.max_upload_bytes,
            fetcher=fetcher,
        )
        first_tool = observe_tool(first_host.as_tools()[0], observed.append, first_host.event_views()["fetch_url"])
        with turn_trace(session_id, uuid4(), enabled=True):
            first = await asyncio.to_thread(first_tool, url=_URL)
        assert first["ok"] is True, first
        assert first["cache_hit"] is False
        assert first["content"] == _BODY

        await resources.session_manager.release(first_lease)
        first_lease = None
        binding = await resources.bindings.get(session_id)
        assert binding is not None
        replacement = await resources.session_manager.replace(
            SandboxBinding(
                session_id=session_id,
                sandbox_id=binding.sandbox_id,
                workspace_id=workspace_id,
                volume_id=binding.volume_id,
                volume_subpath=binding.volume_subpath,
                mount_path=binding.mount_path,
                provider_state="unrecoverable",
            ),
            workspace_id=workspace_id,
            user_id=user_id,
        )
        assert replacement.sandbox_id != binding.sandbox_id
        resources.track_sandbox(replacement.sandbox_id)

        second_lease = await resources.session_manager.acquire(
            LeaseRequest(session_id=session_id, user_id=user_id, workspace_id=workspace_id),
            deadline=asyncio.get_running_loop().time() + 120,
        )
        resources.track_sandbox(second_lease.sandbox_id)
        second_sandbox = await resources.platform.get(second_lease.sandbox_id)
        assert second_sandbox is not None

        second_host = UrlToolHost(
            session_id=session_id,
            store=WorkspaceUrlSourceStore(_workspace(second_sandbox, settings, session_id, asyncio.get_running_loop())),
            max_bytes=settings.max_upload_bytes,
            fetcher=fetcher,
        )
        second_tool = observe_tool(second_host.as_tools()[0], observed.append, second_host.event_views()["fetch_url"])
        with turn_trace(session_id, uuid4(), enabled=True):
            cached = await asyncio.to_thread(second_tool, url=_URL)

        assert cached["ok"] is True
        assert cached["cache_hit"] is True
        assert cached["content"] == _BODY
        assert fetcher.calls == [_URL]
        completed = [item for item in observed if isinstance(item, ToolCompleted)]
        cache_hits: list[bool] = []
        for item in completed:
            assert isinstance(item.output, Mapping)
            cache_hit = item.output.get("cache_hit")
            assert isinstance(cache_hit, bool)
            cache_hits.append(cache_hit)
        assert cache_hits == [False, True]
        assert _BODY not in str(observed)
        assert _URL not in str(observed)
        assert not any("server_url" in str(item.message) for item in captured)
    finally:
        if resources is not None:
            if first_lease is not None:
                await resources.session_manager.release(first_lease)
            if second_lease is not None:
                await resources.session_manager.release(second_lease)
            if cleanup is not None:
                await cleanup.shutdown(drain_seconds=30)
            await resources.adispose()
