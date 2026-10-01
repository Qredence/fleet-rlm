"""Public Daytona runtime facade and client-construction contracts.

This suite covers runtime lifecycle contracts and Daytona 0.218.0 client
construction against the pinned SDK.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from daytona_toolbox_api_client.exceptions import BadRequestException
from pydantic import SecretStr

from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import runtime as runtime_module
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError, classify_provider_error
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.runtime import (
    AbsenceConfirmation,
    AbsenceTimeout,
    ChildRuntimeLease,
    DaytonaAdmission,
    ExpectedWorkspaceMount,
    InterpreterLease,
    LeaseRequest,
    RootSessionSpec,
    SandboxLease,
    SandboxLeasePolicy,
    build_daytona_client,
)
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError
from tests.support.session_manager import _FakePlatform, _FakeSandbox, make_daytona_runtime


@pytest.mark.asyncio
async def test_reused_root_probe_failure_never_replaces_the_sandbox() -> None:
    """A probe fault on a live reused Sandbox must not be answered by deleting it.

    Regression: the bound-Sandbox retry branch called `_replace_bound_sandbox`, which
    fences the binding and *deletes* the Sandbox (`provider_action="delete"`), losing
    session namespace for a probe that only proves `os.chdir` failed once. This drives
    the real verification chain -- `_verify_run_layout` is not mocked -- so the failure
    comes from the actual probe rather than a hand-built error.
    """
    runtime = make_daytona_runtime()
    workspace_id = uuid4()
    expected = ExpectedWorkspaceMount(
        volume_id="vol-1",
        volume_subpath="workspaces/one",
        mount_path="/workspace",
        workspace_id=workspace_id,
    )
    sandbox = _FakeSandbox(
        "sb-live",
        volume_id="vol-1",
        mount_path="/workspace",
        volume_subpath="workspaces/one",
        labels={"workspace_id": str(workspace_id)},
    )
    sandbox.process.exec.return_value = SimpleNamespace(exit_code=1)
    context = runtime_module._AcquisitionContext(expected=expected, binding=SimpleNamespace(sandbox_id="sb-live"))
    request = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=workspace_id)
    runtime._resolve_acquisition_context = AsyncMock(return_value=context)  # type: ignore[method-assign]
    runtime._prepare_sandbox = AsyncMock(return_value=(sandbox, False))  # type: ignore[method-assign]
    runtime._replace_bound_sandbox = AsyncMock()  # type: ignore[method-assign]
    runtime._create_sandbox = AsyncMock()  # type: ignore[method-assign]
    cleanup = AsyncMock(return_value=True)
    runtime._cleanup_failed_acquisition = cleanup  # type: ignore[method-assign]

    with pytest.raises(DaytonaAdapterError) as error:
        await runtime._acquire_provider(request, run_id=uuid4())

    assert error.value.cause_type == "ExecutionMountNotVisible"
    runtime._replace_bound_sandbox.assert_not_awaited()
    runtime._create_sandbox.assert_not_awaited()
    assert cleanup.await_args.kwargs["created_sandbox"] is False


@pytest.mark.asyncio
async def test_volume_layout_fault_is_not_treated_as_a_probe_failure() -> None:
    """A genuinely absent Volume must not be answered by recreating the Sandbox.

    `_require_directory(create=False)` reports the missing mount from the layout step,
    and it can fire on a Sandbox this acquisition just created. Recreating mounts the
    same volume, so the retry cannot repair it: acquisition must fail once, with no
    second create and no second verification pass.
    """
    runtime = make_daytona_runtime()
    context = runtime_module._AcquisitionContext(expected=SimpleNamespace(), binding=SimpleNamespace(sandbox_id="old"))
    request = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    runtime._resolve_acquisition_context = AsyncMock(return_value=context)  # type: ignore[method-assign]
    runtime._prepare_sandbox = AsyncMock(  # type: ignore[method-assign]
        return_value=(SimpleNamespace(id="old"), True)
    )
    runtime._verify_run_layout = AsyncMock(  # type: ignore[method-assign]
        side_effect=DaytonaAdapterError("volume missing", cause_type="VolumeLayoutMissingMount")
    )
    runtime._replace_bound_sandbox = AsyncMock()  # type: ignore[method-assign]
    runtime._create_sandbox = AsyncMock()  # type: ignore[method-assign]
    runtime._cleanup_failed_acquisition = AsyncMock(return_value=True)  # type: ignore[method-assign]

    with pytest.raises(DaytonaAdapterError) as error:
        await runtime._acquire_provider(request, run_id=uuid4())

    assert error.value.cause_type == "VolumeLayoutMissingMount"
    runtime._verify_run_layout.assert_awaited_once()
    runtime._replace_bound_sandbox.assert_not_awaited()
    runtime._create_sandbox.assert_not_awaited()
    runtime._cleanup_failed_acquisition.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_root_with_missing_process_mount_is_retired_before_one_retry() -> None:
    runtime = make_daytona_runtime()
    expected = SimpleNamespace(volume_id="vol", mount_path="/workspace", volume_subpath="workspaces/test")
    context = runtime_module._AcquisitionContext(expected=expected, binding=None)
    old, new = SimpleNamespace(id="old"), SimpleNamespace(id="new")
    request = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    lease = object()
    runtime._resolve_acquisition_context = AsyncMock(return_value=context)  # type: ignore[method-assign]
    runtime._prepare_sandbox = AsyncMock(return_value=(old, True))  # type: ignore[method-assign]
    runtime._cleanup_failed_acquisition = AsyncMock(return_value=True)  # type: ignore[method-assign]
    runtime._create_sandbox = AsyncMock(return_value=new)  # type: ignore[method-assign]
    missing = DaytonaAdapterError("mount missing", cause_type="ExecutionMountNotVisible")
    runtime._verify_run_layout = AsyncMock(side_effect=[missing, None])  # type: ignore[method-assign]
    runtime._persist_binding_and_build_lease = AsyncMock(return_value=lease)  # type: ignore[method-assign]

    assert await runtime._acquire_provider(request, run_id=uuid4()) is lease
    runtime._cleanup_failed_acquisition.assert_awaited_once()
    runtime._create_sandbox.assert_awaited_once()
    assert runtime._verify_run_layout.await_args_list[1].args[0] is new


@pytest.mark.asyncio
async def test_missing_mount_does_not_retry_when_retirement_is_unconfirmed() -> None:
    runtime = make_daytona_runtime()
    context = runtime_module._AcquisitionContext(expected=SimpleNamespace(), binding=None)
    request = LeaseRequest(session_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    runtime._resolve_acquisition_context = AsyncMock(return_value=context)  # type: ignore[method-assign]
    runtime._prepare_sandbox = AsyncMock(return_value=(SimpleNamespace(id="old"), True))  # type: ignore[method-assign]
    runtime._verify_run_layout = AsyncMock(  # type: ignore[method-assign]
        side_effect=DaytonaAdapterError("mount missing", cause_type="ExecutionMountNotVisible")
    )
    runtime._cleanup_failed_acquisition = AsyncMock(return_value=False)  # type: ignore[method-assign]
    runtime._create_sandbox = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(DaytonaAdapterError) as error:
        await runtime._acquire_provider(request, run_id=uuid4())
    assert error.value.cause_type == "SandboxRetirementUnconfirmed"
    runtime._create_sandbox.assert_not_awaited()


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


def test_interpreter_release_does_not_retry_a_body_type_error() -> None:
    calls: list[bool] = []

    class TypeErrorInterpreter:
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            calls.append(strict_broker_cleanup)
            raise TypeError("failure inside shutdown")

    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=TypeErrorInterpreter(),
    )

    with pytest.raises(TypeError, match="failure inside shutdown"):
        lease.release()

    assert calls == [True]
    assert lease.failed


def test_interpreter_release_supports_no_argument_shutdown() -> None:
    calls: list[None] = []

    class LegacyInterpreter:
        def shutdown(self) -> None:
            calls.append(None)

    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=LegacyInterpreter(),
    )

    lease.release()

    assert calls == [None]
    assert lease.closed


def test_interpreter_release_tries_opaque_shutdown_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    class OpaqueInterpreter:
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            calls.append(strict_broker_cleanup)
            raise TypeError("opaque shutdown body failed")

    actual_signature = runtime_module.inspect.signature

    def unavailable_signature(callable_obj: object) -> object:
        if getattr(callable_obj, "__name__", None) == "shutdown":
            raise ValueError("signature unavailable")
        return actual_signature(callable_obj)

    monkeypatch.setattr(runtime_module.inspect, "signature", unavailable_signature)
    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=OpaqueInterpreter(),
    )

    with pytest.raises(TypeError, match="opaque shutdown body failed"):
        lease.release()

    assert calls == [True]
    assert lease.failed


def test_interpreter_release_rejects_unsupported_shutdown_signature() -> None:
    calls: list[object] = []

    class UnsupportedInterpreter:
        def shutdown(self, required: object) -> None:
            calls.append(required)

    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=UnsupportedInterpreter(),
    )

    with pytest.raises(TypeError):
        lease.release()

    assert calls == []
    assert lease.failed


def test_interpreter_release_rejects_non_callable_shutdown_attribute() -> None:
    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=SimpleNamespace(shutdown=None),
    )

    with pytest.raises(TypeError, match="shutdown attribute is not callable"):
        lease.release()

    assert lease.failed


def test_sandbox_lease_shutdown_preserves_body_type_error() -> None:
    calls: list[bool] = []

    class TypeErrorInterpreter:
        broker = object()
        _backend = object()

        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            calls.append(strict_broker_cleanup)
            raise TypeError("failure inside shutdown")

    owner = make_daytona_runtime()
    lease = SandboxLease(
        owner=owner,
        sandbox=None,
        interpreter=TypeErrorInterpreter(),
        policy=SandboxLeasePolicy(kind="retained_session"),
    )

    outcome = lease._shutdown_interpreter()

    assert outcome.status == "failed"
    assert calls == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [TypeError, RuntimeError])
async def test_root_retains_admission_when_real_broker_delete_fails_until_quarantined(
    error_type: type[Exception],
) -> None:
    class Process:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_session(self, session: str) -> None:
            self.deleted.append(session)
            if len(self.deleted) == 1:
                raise error_type("provider delete failed")

    process = Process()
    sandbox = SimpleNamespace(id="sandbox-1", process=process)
    backend = sandbox_backend(sandbox)
    broker = DaytonaHttpToolBroker(sandbox, port=1)
    broker._session = "fleet-tool-broker-runtime-test"
    broker._client = SimpleNamespace(close=lambda: None)
    backend._broker = broker
    interpreter = DaytonaCodeInterpreter(backend=backend)
    session_id, workspace_id, user_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    lease = InterpreterLease(
        sandbox_id="sandbox-1",
        interpreter_id="interpreter-1",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=interpreter,
        sandbox=sandbox,
        session_id=str(session_id),
        workspace_id=str(workspace_id),
        user_id=str(user_id),
        run_id=str(run_id),
        created_sandbox=False,
    )
    admission = DaytonaAdmission(max_active_leases=2)
    platform = _FakePlatform()
    platform.sandboxes["sandbox-1"] = _FakeSandbox("sandbox-1")
    runtime = make_daytona_runtime(admission=admission, platform=platform)
    runtime._acquire_provider = AsyncMock(return_value=lease)  # type: ignore[method-assign]
    await runtime.acquire_root_session(
        RootSessionSpec(session_id=session_id, workspace_id=workspace_id, user_id=user_id)
    )

    with pytest.raises(error_type, match="provider delete failed"):
        await runtime.release(lease)

    assert lease.failed
    assert id(lease) in runtime._late_owners
    assert process.deleted == ["fleet-tool-broker-runtime-test"]
    with pytest.raises(runtime_module.DaytonaAdmissionTimeoutError):
        await admission.acquire(deadline=asyncio.get_running_loop().time() + 0.02)

    await runtime.release(lease)

    # The runtime contains an unconfirmed broker shutdown by fencing the
    # owning Sandbox; it does not replay the failed SDK deletion call.
    assert process.deleted == ["fleet-tool-broker-runtime-test"]
    assert lease.closed
    assert id(lease) not in runtime._late_owners
    assert platform.sandboxes["sandbox-1"].state == "stopped"
    fencing_ops = tuple(platform.sandboxes["sandbox-1"].ops)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()

    assert await runtime.aclose()
    assert runtime.roots == ()
    record = runtime.session_record(workspace_id, session_id)
    assert record is not None and record.root is None
    assert tuple(platform.sandboxes["sandbox-1"].ops) == fencing_ops
    assert process.deleted == ["fleet-tool-broker-runtime-test"]
    assert await runtime.aclose()
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 1)
    permit.release()


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
        volume_config=SimpleNamespace(paths=object),
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
        volume_config=SimpleNamespace(paths=object),
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
        assert version("daytona") == "0.218.0"
        assert client._api_url == "https://app.daytona.io/api"
        assert client._api_client.default_headers["X-Daytona-Organization-ID"] == "test-org"
        assert not any("server_url" in str(item.message) for item in caught)
    finally:
        await client.close()
