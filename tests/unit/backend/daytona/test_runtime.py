"""Public Daytona runtime facade and client-construction contracts.

This suite covers runtime lifecycle contracts and Daytona 0.210.0 client
construction against the pinned SDK.
"""

from __future__ import annotations

import asyncio
import warnings
from importlib.metadata import version
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import runtime as runtime_module
from fleet_rlm.daytona.runtime import (
    AbsenceConfirmation,
    AbsenceTimeout,
    ChildEnvironmentSpec,
    ChildRuntimeLease,
    DaytonaAdmission,
    DaytonaRuntime,
    RootSessionSpec,
    build_daytona_client,
)
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError


# --- Runtime lifecycle -------------------------------------------------
@pytest.mark.asyncio
async def test_runtime_close_retains_a_failed_root_for_retry() -> None:
    calls = 0

    async def release(_lease: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider close failed")

    runtime = DaytonaRuntime(
        root_acquirer=lambda _spec, **_kwargs: object(),
        root_releaser=release,
    )
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    await runtime.acquire_root_session(spec)

    assert await runtime.aclose() is False
    assert len(runtime.roots) == 1
    assert runtime.state.value == "FAILED"

    assert await runtime.aclose() is True
    assert runtime.roots == ()
    assert calls == 2


@pytest.mark.asyncio
async def test_successful_child_close_deregisters_from_runtime() -> None:
    class Lease:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    lease = Lease()
    runtime = DaytonaRuntime(child_acquirer=lambda _spec: lease)
    spec = ChildEnvironmentSpec()

    async with runtime.open_child(spec):
        assert len(runtime.children) == 1

    assert runtime.children == ()
    assert lease.close_calls == 1


@pytest.mark.asyncio
async def test_runtime_owned_child_factory_registers_and_closes_child_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    close_calls: list[str] = []

    class ProviderFactory:
        def __call__(self, call_index: int, *, profile: object = None) -> ChildRuntimeLease:
            assert call_index == 4
            assert profile is None
            return ChildRuntimeLease(
                SimpleNamespace(),
                "child-sandbox",
                "child-volume",
                "recursive/workspace/run/4",
                lambda: close_calls.append("closed"),
            )

        def wait_owned(self) -> None:
            return None

        def raise_if_cleanup_failed(self) -> None:
            return None

    monkeypatch.setattr(runtime_module, "_build_child_runtime_factory", lambda **_kwargs: ProviderFactory())
    runtime = DaytonaRuntime()
    factory = runtime.build_child_factory(deadline=loop.time() + 5)

    lease = await asyncio.to_thread(factory, 4)

    assert runtime.has_pending_ownership
    assert lease in runtime._child_runtime_leases
    assert await runtime.aclose(deadline=loop.time() + 5)
    assert lease.state.value == "CLOSED"
    assert close_calls == ["closed"]
    assert not runtime.has_pending_ownership


@pytest.mark.asyncio
async def test_child_runtime_uses_configured_rlm_execution_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def build_factory(**kwargs: object) -> object:
        captured.update(kwargs)

        def create(_call_index: int, *, profile: object = None) -> ChildRuntimeLease:
            assert profile is None
            return ChildRuntimeLease(SimpleNamespace(), "child-lease", "volume", "scope", lambda: None)

        return create

    monkeypatch.setattr(runtime_module, "_build_child_runtime_factory", build_factory)
    settings = SimpleNamespace(rlm_execution_timeout_s=37, rlm_max_execution_output_chars=1234)
    resources = SimpleNamespace(
        platform=object(),
        daytona_admission=object(),
        settings=settings,
        dispatcher=None,
    )
    runtime = DaytonaRuntime(resources)
    spec = ChildEnvironmentSpec(
        workspace_id=uuid4(),
        run_id=uuid4(),
        volume_id="volume",
        mount_path="/home/daytona/fleet",
        call_index=4,
    )

    lease = await runtime._acquire_child_from_resources(spec)
    assert lease.sandbox_id == "child-lease"
    assert captured["execution_timeout_s"] == 37
    assert captured["execution_output_cap"] == 1234


@pytest.mark.asyncio
async def test_workspace_io_sandbox_is_runtime_owned_through_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = SimpleNamespace(id="io-sandbox")

    async def refresh_data() -> None:
        return None

    sandbox.refresh_data = refresh_data

    class Platform:
        def __init__(self) -> None:
            self.deleted = False

        async def delete(self, sandbox_id: str) -> None:
            assert sandbox_id == sandbox.id
            self.deleted = True

        async def get(self, _sandbox_id: str) -> object | None:
            return None if self.deleted else sandbox

    platform = Platform()
    admission = DaytonaAdmission(max_active_leases=1)
    runtime = DaytonaRuntime(
        platform=platform,
        volume_client=object(),
        volume_config=SimpleNamespace(paths=lambda: object()),
        admission=admission,
    )

    async def volume_id(*_args: object) -> str:
        return "volume"

    async def create(*_args: object, **_kwargs: object) -> object:
        return sandbox

    async def layout(*_args: object) -> None:
        return None

    monkeypatch.setattr(runtime_module, "get_or_create_volume_id", volume_id)
    monkeypatch.setattr(runtime_module, "sandbox_state", lambda _sandbox: "running")
    monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", layout)
    runtime._provisioner = SimpleNamespace(
        expected_mount=lambda **_kwargs: object(), create=create, verify=lambda *_: None
    )

    async with runtime.open_workspace_sandbox(uuid4(), purpose="test") as acquired:
        assert acquired is sandbox
        assert runtime.has_pending_ownership
        assert admission._semaphore._value == 0
        assert not await runtime.aclose(deadline=asyncio.get_running_loop().time() + 0.01)
        assert not platform.deleted

    assert platform.deleted
    assert admission._semaphore._value == 1
    assert not runtime._workspace_io_leases
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_workspace_io_unconfirmed_delete_retains_permit_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = SimpleNamespace(id="io-unconfirmed")

    async def refresh_data() -> None:
        return None

    sandbox.refresh_data = refresh_data
    confirmed = False

    class Platform:
        async def delete(self, _sandbox_id: str) -> None:
            return None

        async def get(self, _sandbox_id: str) -> object | None:
            return None if confirmed else sandbox

    async def confirm(**_kwargs: object) -> AbsenceConfirmation | AbsenceTimeout:
        if confirmed:
            return AbsenceConfirmation(sandbox.id, ("absent",), 0.0)
        return AbsenceTimeout(sandbox.id, "running", ("running",), 0.0)

    async def volume_id(*_args: object) -> str:
        return "volume"

    async def create(*_args: object, **_kwargs: object) -> object:
        return sandbox

    async def layout(*_args: object) -> None:
        return None

    monkeypatch.setattr(runtime_module, "confirm_absence", confirm)
    monkeypatch.setattr(runtime_module, "get_or_create_volume_id", volume_id)
    monkeypatch.setattr(runtime_module, "sandbox_state", lambda _sandbox: "running")
    monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", layout)
    admission = DaytonaAdmission(max_active_leases=1)
    runtime = DaytonaRuntime(
        platform=Platform(),
        volume_client=object(),
        volume_config=SimpleNamespace(paths=lambda: object()),
        admission=admission,
    )
    runtime._provisioner = SimpleNamespace(
        expected_mount=lambda **_kwargs: object(), create=create, verify=lambda *_: None
    )

    async with runtime.open_workspace_sandbox(uuid4(), purpose="test"):
        pass

    assert admission._semaphore._value == 0
    assert runtime.has_pending_ownership
    confirmed = True
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert admission._semaphore._value == 1
    assert not runtime._workspace_io_leases


@pytest.mark.asyncio
async def test_workspace_io_cancelled_create_is_settled_by_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    finish = asyncio.Event()
    deleted: list[str] = []
    sandbox = SimpleNamespace(id="late-io")

    class Platform:
        async def delete(self, sandbox_id: str) -> None:
            deleted.append(sandbox_id)

        async def get(self, _sandbox_id: str) -> None:
            return None

    async def volume_id(*_args: object) -> str:
        return "volume"

    async def create(*_args: object, **_kwargs: object) -> object:
        entered.set()
        await finish.wait()
        return sandbox

    monkeypatch.setattr(runtime_module, "get_or_create_volume_id", volume_id)
    admission = DaytonaAdmission(max_active_leases=1)
    runtime = DaytonaRuntime(platform=Platform(), volume_client=object(), volume_config=object(), admission=admission)
    runtime._provisioner = SimpleNamespace(expected_mount=lambda **_kwargs: object(), create=create)

    async def use_workspace() -> None:
        async with runtime.open_workspace_sandbox(uuid4(), purpose="test"):
            pass

    task = asyncio.create_task(use_workspace())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert admission._semaphore._value == 0
    assert runtime.has_pending_ownership

    finish.set()
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert deleted == ["late-io"]
    assert admission._semaphore._value == 1


@pytest.mark.asyncio
async def test_process_disposal_keeps_client_open_until_tracked_absence() -> None:
    present = True
    steps: list[str] = []

    class Platform:
        async def delete(self, sandbox_id: str) -> None:
            steps.append(f"delete:{sandbox_id}")

        async def get(self, sandbox_id: str) -> object | None:
            steps.append(f"probe:{sandbox_id}")
            return object() if present else None

    class Client:
        async def close(self) -> None:
            steps.append("client-close")

    runtime = DaytonaRuntime(platform=Platform(), client=Client())
    runtime.track_sandbox("tracked")

    assert not await runtime.adispose(drain_seconds=1)
    assert "client-close" not in steps
    assert runtime._tracked_sandbox_ids == ["tracked"]

    present = False
    assert await runtime.adispose(drain_seconds=1)
    assert steps[-1] == "client-close"
    assert runtime._tracked_sandbox_ids == []


@pytest.mark.asyncio
async def test_child_unconfirmed_delete_keeps_capacity_until_runtime_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    confirmed = False

    class Fs:
        async def list_files(self, _path: str, *, depth: int | None) -> list[object]:
            assert depth is None
            return []

    sandbox = SimpleNamespace(id="child-unconfirmed", fs=Fs())

    class Platform:
        async def create(self, **_kwargs: object) -> object:
            return sandbox

        async def delete(self, _sandbox_id: str) -> None:
            return None

        async def get(self, _sandbox_id: str) -> object | None:
            return None if confirmed else sandbox

    class Interpreter:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            assert strict_broker_cleanup

    async def confirm(**_kwargs: object) -> AbsenceConfirmation | AbsenceTimeout:
        if confirmed:
            return AbsenceConfirmation(sandbox.id, ("absent",), 0.0)
        return AbsenceTimeout(sandbox.id, "running", ("running",), 0.0)

    monkeypatch.setattr(runtime_module, "DaytonaCodeInterpreter", Interpreter)
    monkeypatch.setattr(runtime_module, "sandbox_backend", lambda child, **_kwargs: child)
    monkeypatch.setattr(runtime_module, "confirm_absence", confirm)
    loop = asyncio.get_running_loop()
    admission = DaytonaAdmission(max_active_leases=1)
    runtime = DaytonaRuntime()
    factory = runtime.build_child_factory(
        platform=Platform(),
        admission=admission,
        volume_id="volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 5,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )
    lease = await asyncio.to_thread(factory, 1)

    with pytest.raises(ChildRuntimeCleanupError):
        await asyncio.to_thread(lease.close)
    assert admission._semaphore._value == 0
    assert "child-unconfirmed" in runtime._child_cleanup_records

    confirmed = True
    await runtime.aclose(deadline=loop.time() + 5)
    assert admission._semaphore._value == 1
    assert runtime._child_cleanup_records == {}
    assert await runtime.aclose(deadline=loop.time() + 5)


# --- Pinned SDK client construction -----------------------------------
@pytest.mark.asyncio
async def test_build_daytona_client_uses_explicit_api_url_without_deprecation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAYTONA_API_URL", "https://ambient.example/api")
    settings = Settings(
        daytona_api_key=SecretStr("test-daytona-key"),
        daytona_org_id="test-org",
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = build_daytona_client(settings)

    try:
        assert version("daytona") == "0.210.0"
        assert client._api_url == "https://app.daytona.io/api"
        assert client._api_client.default_headers["X-Daytona-Organization-ID"] == "test-org"
        assert not any("server_url" in str(item.message) for item in caught)
    finally:
        await client.close()
