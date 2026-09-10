from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from fleet_rlm.daytona.session_manager import DaytonaSessionManager, InterpreterLease, LeaseRequest
from fleet_rlm.runtime.bindings import BindingGenerationAuthority, SandboxBinding


def test_revoked_generation_is_not_resurrected_by_stale_durable_read() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    authority = BindingGenerationAuthority()
    running = SandboxBinding(
        session_id=session_id,
        sandbox_id="sandbox-1",
        workspace_id=workspace_id,
        volume_id="volume-1",
        volume_subpath=f"workspaces/{workspace_id}",
        mount_path="/home/daytona/fleet",
        provider_state="running",
        generation=1,
    )

    authority.observe(running)
    authority.revoke(
        session_id=session_id,
        workspace_id=workspace_id,
        sandbox_id=running.sandbox_id or "",
        generation=running.generation,
    )
    authority.observe(running)  # stale read from before the revoke

    assert not authority.is_current(
        session_id=session_id,
        workspace_id=workspace_id,
        sandbox_id="sandbox-1",
        generation=1,
    )

    replacement = replace(running, sandbox_id="sandbox-2", generation=2)
    authority.observe(replacement)
    assert authority.is_current(
        session_id=session_id,
        workspace_id=workspace_id,
        sandbox_id="sandbox-2",
        generation=2,
    )


@pytest.mark.parametrize("fenced_state", ["fencing", "quarantined", "stopped"])
def test_non_running_generation_is_not_resurrected_by_stale_running_read(fenced_state: str) -> None:
    """A delayed same-generation read cannot re-arm a fenced native process."""
    session_id, workspace_id = uuid4(), uuid4()
    authority = BindingGenerationAuthority()
    running = SandboxBinding(
        session_id=session_id,
        sandbox_id="sandbox-1",
        workspace_id=workspace_id,
        volume_id="volume-1",
        volume_subpath=f"workspaces/{workspace_id}",
        mount_path="/home/daytona/fleet",
        provider_state="running",
        generation=1,
    )

    authority.observe(running)
    authority.observe(replace(running, provider_state=fenced_state))
    authority.observe(running)  # stale row read from before the fence

    assert not authority.is_current(
        session_id=session_id,
        workspace_id=workspace_id,
        sandbox_id=running.sandbox_id or "",
        generation=running.generation,
    )


@pytest.mark.asyncio
async def test_stale_native_cleanup_cannot_overwrite_replacement_binding() -> None:
    session_id, workspace_id = uuid4(), uuid4()
    replacement = SandboxBinding(
        session_id=session_id,
        sandbox_id="replacement-sandbox",
        workspace_id=workspace_id,
        volume_id="volume-1",
        volume_subpath=f"workspaces/{workspace_id}",
        mount_path="/home/daytona/fleet",
        provider_state="running",
        generation=2,
    )

    class Store:
        def __init__(self) -> None:
            self.upserts: list[SandboxBinding] = []

        async def get_scoped(self, session_id, *, workspace_id):
            assert session_id == replacement.session_id
            assert workspace_id == replacement.workspace_id
            return replacement

        async def get(self, session_id):
            assert session_id == replacement.session_id
            return replacement

        async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
            self.upserts.append(binding)
            return binding

    store = Store()
    manager = DaytonaSessionManager.__new__(DaytonaSessionManager)
    manager._bindings = store  # type: ignore[assignment]
    manager._provider_tasks = set()
    old_lease = InterpreterLease(
        sandbox_id="old-sandbox",
        interpreter_id="old-interpreter",
        volume_id="volume-1",
        mount_path="/home/daytona/fleet",
        interpreter=cast(Any, SimpleNamespace()),
        binding_generation=1,
    )
    request = LeaseRequest(session_id=session_id, user_id=uuid4(), workspace_id=workspace_id)

    await manager._persist_native_binding_state(old_lease, request, provider_state="quarantined")

    assert store.upserts == []
    assert replacement.sandbox_id == "replacement-sandbox"
    assert replacement.generation == 2
