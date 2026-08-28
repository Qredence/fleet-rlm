"""Provider-local Daytona root lease reuse tests (P45)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona.admission import DaytonaAdmission
from fleet_rlm.runtime.daytona.run_environment import _DaytonaEnvironmentProvider, _ResidentRootLease
from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
from fleet_rlm.workspace.paths import volume_paths_from_settings


def _turn(*, session_id=None, workspace_id=None, attachment_ids=(), skill_selections=()):
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken

    async def not_cancelled() -> bool:
        return False

    session = session_id or uuid4()
    workspace = workspace_id or uuid4()
    return ClaimedRun(
        uuid4(),
        session,
        TurnAccess(uuid4(), workspace),
        TurnInput(
            "inspect the workspace",
            attachment_ids=tuple(attachment_ids),
            skill_selections=tuple(skill_selections),
        ),
        SessionHistory(()),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


class _Sink:
    def __init__(self, sandbox, **_kwargs):
        self.sandbox = sandbox
        self.volume_fs = SimpleNamespace(sandbox=sandbox)


class _SessionManager:
    def __init__(self) -> None:
        self.acquired: list[object] = []
        self.released: list[object] = []

    async def acquire(self, request, *, deadline):
        del deadline
        lease = SimpleNamespace(
            sandbox_id=f"sandbox-{len(self.acquired) + 1}",
            interpreter=object(),
            volume_id="volume",
            request=request,
        )
        self.acquired.append(lease)
        return lease

    async def release(self, lease) -> None:
        self.released.append(lease)


class _Platform:
    def __init__(self) -> None:
        self.lookups: list[str] = []
        self.sandboxes: dict[str, object] = {}
        self.return_none = False

    async def get(self, sandbox_id: str):
        self.lookups.append(sandbox_id)
        if self.return_none:
            return None
        return self.sandboxes.setdefault(sandbox_id, object())


def _provider(monkeypatch: pytest.MonkeyPatch):
    import fleet_rlm.runtime.daytona.run_environment as module
    import fleet_rlm.workspace.memory as workspace_memory

    manager = _SessionManager()
    platform = _Platform()
    settings = Settings(run_environment="daytona")
    monkeypatch.setattr(module, "_DaytonaRunSink", _Sink)
    monkeypatch.setattr(workspace_memory, "build_workspace_memory_store", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "build_child_runtime_factory", lambda **_kwargs: object())
    resources = SimpleNamespace(
        session_manager=manager,
        platform=platform,
        volume_paths=volume_paths_from_settings(settings),
        volume_config=SimpleNamespace(mount_path=settings.volume_mount_path),
        daytona_admission=DaytonaAdmission(max_active_leases=2),
        track_sandbox=lambda _sandbox_id: None,
    )
    return _DaytonaEnvironmentProvider(cast(Any, resources), settings), manager, platform


@pytest.mark.asyncio
async def test_root_lease_reuses_workspace_session_but_rebuilds_turn_sinks(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, manager, platform = _provider(monkeypatch)
    session_id = uuid4()
    workspace_id = uuid4()
    first = await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))
    await first.release()
    second = await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))

    assert len(manager.acquired) == 1
    assert len(platform.lookups) == 2
    assert first.interpreter is second.interpreter
    assert first.attachment_sink is not second.attachment_sink
    assert first.artifact_sink is not second.artifact_sink
    assert first.resident_release is not None
    assert second.resident_release is None

    # A reused Turn's release is deliberately per-Turn no-op. The resident
    # callback remains the sole owner of the shared provider lease.
    await second.release()
    assert manager.released == []
    await first.resident_release()  # type: ignore[misc]
    await first.resident_release()  # type: ignore[misc]
    assert manager.released == [manager.acquired[0]]
    assert provider._resident_root_leases == {}


@pytest.mark.asyncio
async def test_same_attachment_id_rotates_run_scoped_manifest_root(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, manager, _platform = _provider(monkeypatch)
    session_id = uuid4()
    workspace_id = uuid4()
    attachment_id = uuid4()

    first = await provider.acquire(
        _turn(session_id=session_id, workspace_id=workspace_id, attachment_ids=(attachment_id,)),
        deadline=float("inf"),
    )
    await first.release()
    second = await provider.acquire(
        _turn(session_id=session_id, workspace_id=workspace_id, attachment_ids=(attachment_id,)),
        deadline=float("inf"),
    )

    # Attachment manifests contain Run-scoped paths.  The interpreter cannot
    # replace a bound manifest, so the root must rotate even when the durable
    # Attachment ID is unchanged.
    assert len(manager.acquired) == 2
    assert manager.released == [manager.acquired[0]]
    assert first.interpreter is not second.interpreter
    await provider.aclose()
    assert manager.released == [manager.acquired[0], manager.acquired[1]]


@pytest.mark.asyncio
async def test_context_selector_change_rotates_provider_root_before_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, manager, platform = _provider(monkeypatch)
    session_id = uuid4()
    workspace_id = uuid4()
    first = await provider.acquire(
        _turn(session_id=session_id, workspace_id=workspace_id, attachment_ids=(uuid4(),)),
        deadline=float("inf"),
    )
    await first.release()
    second = await provider.acquire(
        _turn(session_id=session_id, workspace_id=workspace_id, attachment_ids=(uuid4(),)),
        deadline=float("inf"),
    )

    assert len(manager.acquired) == 2
    assert manager.released == [manager.acquired[0]]
    assert first.interpreter is not second.interpreter
    assert len(platform.lookups) == 2
    await provider.aclose()
    assert manager.released == [manager.acquired[0], manager.acquired[1]]


@pytest.mark.asyncio
async def test_reused_provider_failure_quarantines_root(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, manager, platform = _provider(monkeypatch)
    session_id = uuid4()
    workspace_id = uuid4()
    first = await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))
    await first.release()
    platform.return_none = True

    with pytest.raises(RuntimeError, match="Sandbox is unavailable"):
        await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))

    assert manager.released == [manager.acquired[0]]
    assert provider._resident_root_leases == {}


@pytest.mark.asyncio
async def test_resident_root_close_survives_caller_cancellation() -> None:
    import asyncio

    lease = object()
    entered = asyncio.Event()
    release = asyncio.Event()
    releases: list[object] = []
    closed: list[_ResidentRootLease] = []

    async def release_callback(value: object) -> None:
        releases.append(value)
        entered.set()
        await release.wait()

    async def on_closed(owner: _ResidentRootLease) -> None:
        closed.append(owner)

    owner = _ResidentRootLease((uuid4(), uuid4()), lease, release_callback, on_closed)
    first = asyncio.create_task(owner.close())
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert owner.closed is False
    release.set()
    await owner.close()
    assert owner.closed is True
    assert releases == [lease]
    assert closed == [owner]


@pytest.mark.asyncio
async def test_cancelled_lookup_returns_promptly_and_retains_root_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    provider, manager, platform = _provider(monkeypatch)
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()

    async def slow_get(_sandbox_id: str) -> object:
        lookup_started.set()
        await release_lookup.wait()
        return object()

    monkeypatch.setattr(platform, "get", slow_get)
    run = _turn()
    acquisition = asyncio.create_task(provider.acquire(run, deadline=float("inf")))
    await lookup_started.wait()

    acquisition.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(acquisition, timeout=0.2)

    assert manager.released == []
    assert provider.has_pending_acquisitions
    release_lookup.set()
    owned = tuple(provider._late_lookup_tasks)
    if owned:
        await asyncio.gather(*owned)
    else:
        await asyncio.sleep(0)
    assert manager.released
    assert not provider._resident_root_leases


@pytest.mark.asyncio
async def test_root_lease_key_includes_workspace_and_session(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, manager, _platform = _provider(monkeypatch)
    first_turn = _turn()
    second_turn = _turn()
    third_turn = _turn(session_id=first_turn.session_id, workspace_id=uuid4())
    first_env = await provider.acquire(first_turn, deadline=float("inf"))
    await first_env.release()
    await provider.acquire(second_turn, deadline=float("inf"))
    await provider.acquire(third_turn, deadline=float("inf"))

    assert len(manager.acquired) == 3
    assert len(provider._resident_root_leases) == 3
    await provider.aclose()
    assert len(manager.released) == 3


@pytest.mark.asyncio
async def test_resident_root_release_and_close_are_idempotent() -> None:
    lease = object()
    releases: list[object] = []
    closed: list[object] = []

    async def release(value: object) -> None:
        releases.append(value)

    async def on_closed(owner: _ResidentRootLease) -> None:
        closed.append(owner)

    owner = _ResidentRootLease((uuid4(), uuid4()), lease, release, on_closed)
    await owner.release()
    await owner.close()

    assert owner.closed is True
    assert releases == [lease]
    assert closed == [owner]


@pytest.mark.asyncio
async def test_reused_provider_failure_taints_the_session_runtime_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """P52.3(d): provider proof of an unhealthy root taints the Session runtime registry."""
    from fleet_rlm.rlm.session_runtime import SessionKey, SessionRLMRegistry, SessionRLMState

    class _Interpreter:
        def __init__(self) -> None:
            self.namespace: dict[str, object] = {}
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    built: list[SessionRLMState] = []

    async def factory(key: SessionKey, fingerprint: str) -> SessionRLMState:
        state = SessionRLMState(key, fingerprint, object(), _Interpreter())
        built.append(state)
        return state

    registry = SessionRLMRegistry(factory)
    provider, _manager, platform = _provider(monkeypatch)
    provider.session_runtime_registry = registry

    session_id = uuid4()
    workspace_id = uuid4()
    key = SessionKey(workspace_id=str(workspace_id), session_id=str(session_id))
    resident = await registry.acquire(key, "fingerprint-a")

    first = await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))
    await first.release()
    platform.return_none = True

    with pytest.raises(RuntimeError, match="Sandbox is unavailable"):
        await provider.acquire(_turn(session_id=session_id, workspace_id=workspace_id), deadline=float("inf"))

    # The provider's unhealthy-root proof tainted the resident registry state,
    # and the next acquire rotates to a fresh generation.
    assert resident.tainted
    fresh = await registry.acquire(key, "fingerprint-a")
    assert fresh is not resident
    assert len(built) == 2
    assert provider._resident_root_leases == {}
