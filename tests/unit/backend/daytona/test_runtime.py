"""Public Daytona runtime facade and client-construction contracts.

This suite covers runtime lifecycle contracts and Daytona 0.210.0 client
construction against the pinned SDK.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from importlib.metadata import version
from types import SimpleNamespace
from uuid import uuid4

import pytest
from daytona_toolbox_api_client.exceptions import BadRequestException
from pydantic import SecretStr

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import runtime as runtime_module
from fleet_rlm.daytona.errors import ProviderRequestError, classify_provider_error
from fleet_rlm.daytona.runtime import (
    AbsenceConfirmation,
    AbsenceTimeout,
    ChildRuntimeLease,
    DaytonaAdmission,
    InterpreterLease,
    RootSessionSpec,
    build_daytona_client,
)
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError
from tests.support.session_manager import make_daytona_runtime


# --- Runtime lifecycle -------------------------------------------------
@pytest.mark.asyncio
async def test_root_acquisition_maps_sdk_bad_request_without_publishing_a_root() -> None:
    async def acquire(_request: object, **_kwargs: object) -> object:
        raise BadRequestException(status=400, reason="api_key=private")

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    with pytest.raises(ProviderRequestError) as raised:
        await runtime.acquire_root_session(spec)

    assert raised.value.cause_type == "BadRequestException"
    assert raised.value.status_code == 400
    assert classify_provider_error(raised.value) == "request_validation"
    assert "private" not in str(raised.value)
    assert runtime.roots == ()


@pytest.mark.asyncio
async def test_root_acquisition_preserves_application_value_error() -> None:
    async def acquire(_request: object, **_kwargs: object) -> object:
        raise ValueError("invalid local invariant")

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="invalid local invariant"):
        await runtime.acquire_root_session(RootSessionSpec(workspace_id=uuid4(), session_id=uuid4()))


@pytest.mark.asyncio
async def test_interpreter_release_maps_sdk_error_and_retains_cleanup_ownership(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingInterpreter:
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            del strict_broker_cleanup
            raise BadRequestException(status=400, reason="api_key=private")

    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=FailingInterpreter(),
        session_id=str(uuid4()),
        workspace_id=str(uuid4()),
        user_id=str(uuid4()),
        run_id=str(uuid4()),
    )
    runtime = make_daytona_runtime()

    with pytest.raises(ProviderRequestError) as raised, caplog.at_level(logging.WARNING):
        await runtime.release(lease)

    assert raised.value.cause_type == "BadRequestException"
    assert raised.value.status_code == 400
    assert lease.failed
    assert id(lease) in runtime._late_owners
    assert "sandbox_id=sandbox-1 error_type=BadRequestException" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_runtime_close_retains_a_failed_root_for_retry() -> None:
    calls = 0

    class FailingOnceInterpreter:
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            nonlocal calls
            del strict_broker_cleanup
            calls += 1
            if calls == 1:
                raise RuntimeError("provider close failed")

    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=FailingOnceInterpreter(),
        sandbox=SimpleNamespace(id="sandbox-1"),
    )
    runtime = make_daytona_runtime()
    runtime.acquire = lambda _request, **_kwargs: asyncio.sleep(0, result=lease)  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    await runtime.acquire_root_session(spec)

    assert await runtime.aclose() is False
    assert len(runtime.roots) == 1
    assert runtime.state.value == "FAILED"

    assert await runtime.aclose() is True
    assert runtime.roots == ()
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_owned_child_factory_registers_and_closes_child_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    close_calls: list[str] = []
    runtime = make_daytona_runtime()

    async def acquire_child(*, call_index: int, **_kwargs: object) -> ChildRuntimeLease:
        assert call_index == 4
        sandbox_id = "child-sandbox"
        sandbox = SimpleNamespace(id=sandbox_id)
        permit = SimpleNamespace(_released=False)

        def close_child() -> None:
            close_calls.append("closed")
            permit._released = True

        runtime._child_cleanup_records[sandbox_id] = runtime_module._ChildCleanupRecord(
            runtime._platform,
            sandbox,
            sandbox_id,
            None,
            permit,
        )
        return ChildRuntimeLease(
            SimpleNamespace(),
            sandbox_id,
            "child-volume",
            "recursive/workspace/run/4",
            close_child,
        )

    monkeypatch.setattr(runtime, "_acquire_child_runtime", acquire_child)
    factory = runtime.build_child_factory(
        volume_id="child-volume",
        mount_path=None,
        workspace_id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        deadline=loop.time() + 5,
        execution_timeout_s=30,
        execution_output_cap=1000,
    )

    lease = await asyncio.to_thread(factory, 4)

    assert runtime.has_pending_ownership
    assert runtime._child_cleanup_records[lease.sandbox_id].lease is lease
    assert await runtime.aclose(deadline=loop.time() + 5)
    assert lease.state.value == "CLOSED"
    assert close_calls == ["closed"]
    assert not runtime.has_pending_ownership


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
    runtime = make_daytona_runtime(
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
    monkeypatch.setattr(runtime_module, "_expected_workspace_mount", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "_create_daytona_sandbox", create)
    monkeypatch.setattr(runtime_module, "verify_sandbox_workspace_mount", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "verify_sandbox_spec", lambda *_args: None)

    async with runtime.open_workspace_sandbox(uuid4(), purpose="test") as acquired:
        assert acquired is sandbox
        assert runtime.has_pending_ownership
        assert admission._semaphore._value == 0
        assert not await runtime.aclose(deadline=asyncio.get_running_loop().time() + 0.01)
        assert not platform.deleted

    assert platform.deleted
    assert admission._semaphore._value == 1
    assert not runtime._workspace_io_resources()
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
    runtime = make_daytona_runtime(
        platform=Platform(),
        volume_client=object(),
        volume_config=SimpleNamespace(paths=lambda: object()),
        admission=admission,
    )
    monkeypatch.setattr(runtime_module, "_expected_workspace_mount", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "_create_daytona_sandbox", create)
    monkeypatch.setattr(runtime_module, "verify_sandbox_workspace_mount", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "verify_sandbox_spec", lambda *_args: None)

    async with runtime.open_workspace_sandbox(uuid4(), purpose="test"):
        pass

    assert admission._semaphore._value == 0
    assert runtime.has_pending_ownership
    confirmed = True
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert admission._semaphore._value == 1
    assert not runtime._workspace_io_resources()


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
    runtime = make_daytona_runtime(
        platform=Platform(),
        volume_client=object(),
        volume_config=SimpleNamespace(paths=lambda: object()),
        admission=admission,
    )
    monkeypatch.setattr(runtime_module, "_expected_workspace_mount", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "_create_daytona_sandbox", create)
    monkeypatch.setattr(runtime_module, "verify_sandbox_workspace_mount", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "verify_sandbox_spec", lambda *_args: None)

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

    runtime = make_daytona_runtime(platform=Platform(), client=Client())
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
        async def create_folder(self, _path: str, _mode: str) -> None:
            return None

        async def delete_file(self, _path: str, *, recursive: bool = False) -> None:
            del recursive

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

        def bind_run_scratch(self, _run_id: object, *, call_index: int | None = None) -> None:
            del call_index

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
    runtime = make_daytona_runtime(platform=Platform(), admission=admission)
    factory = runtime.build_child_factory(
        volume_id="volume",
        mount_path="/home/daytona/fleet",
        workspace_id=uuid4(),
        session_id=uuid4(),
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
