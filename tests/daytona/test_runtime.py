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
    DaytonaAdmissionTimeoutError,
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


@pytest.fixture
def workspace_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[runtime_module.DaytonaRuntime, SimpleNamespace]:
    live: dict[str, SimpleNamespace] = {}
    created: list[SimpleNamespace] = []
    deleted: list[str] = []
    platform = SimpleNamespace(live=live, created=created, deleted=deleted, confirm_delete=True)

    async def create(*_args: object, **_kwargs: object) -> SimpleNamespace:
        sandbox = SimpleNamespace(id=f"io-{len(created)}", state="running", refresh_data=AsyncMock())
        created.append(sandbox)
        live[sandbox.id] = sandbox
        return sandbox

    async def delete(sandbox_id: str) -> None:
        deleted.append(sandbox_id)
        if platform.confirm_delete:
            live.pop(sandbox_id, None)

    async def get(sandbox_id: str) -> object | None:
        return live.get(sandbox_id)

    async def confirm(*, sandbox_id: str, **_kwargs: object) -> AbsenceConfirmation | AbsenceTimeout:
        if sandbox_id not in live:
            return AbsenceConfirmation(sandbox_id, ("absent",), 0.0)
        return AbsenceTimeout(sandbox_id, "running", ("running",), 0.0)

    platform.delete = delete
    platform.get = get
    platform.start = AsyncMock()
    monkeypatch.setattr(runtime_module, "get_or_create_volume_id", AsyncMock(return_value="volume"))
    monkeypatch.setattr(runtime_module, "_expected_workspace_mount", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "_create_daytona_sandbox", create)
    monkeypatch.setattr(runtime_module, "sandbox_state", lambda sandbox: sandbox.state)
    monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", AsyncMock())
    monkeypatch.setattr(runtime_module, "verify_sandbox_workspace_mount", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "verify_sandbox_spec", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "confirm_absence", confirm)
    runtime = make_daytona_runtime(platform=platform, admission=DaytonaAdmission(max_active_leases=2))
    return runtime, platform


@pytest.mark.asyncio
@pytest.mark.parametrize("reused", [False, True])
async def test_warm_workspace_shutdown_drains_contexts(workspace_runtime, reused: bool) -> None:
    runtime, platform = workspace_runtime
    workspace_id = uuid4()
    if reused:
        async with runtime.open_workspace_sandbox(workspace_id, purpose="prime", reuse_warm=True):
            pass
    async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True):
        assert not await runtime.aclose(drain_seconds=0.01)
        assert not platform.deleted
    assert await runtime.aclose(drain_seconds=1)
    assert platform.deleted == ["io-0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["refresh", "state", "restart"])
async def test_warm_workspace_replacement_closes_obsolete_lease(workspace_runtime, failure: str) -> None:
    runtime, platform = workspace_runtime
    workspace_id = uuid4()
    for _ in range(3):
        async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True) as sandbox:
            pass
        if failure == "refresh":
            sandbox.refresh_data.side_effect = RuntimeError("probe unavailable")
        elif failure == "state":
            sandbox.state = "unknown"
        else:
            sandbox.state = "stopped"
            platform.start.side_effect = RuntimeError("restart failed")
    assert len(platform.created) == 3
    assert platform.deleted == ["io-0", "io-1"]
    assert runtime._admission._semaphore._value == 1
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_warm_workspace_cache_preserves_turn_capacity(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    for _ in range(4):
        async with asyncio.timeout(1):
            async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
                pass
        permit = await runtime._admission.acquire(deadline=asyncio.get_running_loop().time() + 0.1)
        permit.release()
    assert len(runtime._warm_workspace_io_sandboxes) == 1
    assert len(platform.live) == 1
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_warm_workspace_idle_expiry_and_reuse_timer(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_idle_seconds = 0.03
    workspace_id = uuid4()
    async with runtime.open_workspace_sandbox(workspace_id, purpose="prime", reuse_warm=True):
        pass
    async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True):
        await asyncio.sleep(0.05)
        assert not platform.deleted
    async with asyncio.timeout(1):
        while not platform.deleted:
            await asyncio.sleep(0.01)
    assert not runtime._warm_workspace_io_sandboxes
    assert runtime._admission._semaphore._value == 2
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_warm_workspace_failed_cleanup_blocks_replacement(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_acquisition_timeout_seconds = 0.03
    async with runtime.open_workspace_sandbox(uuid4(), purpose="prime", reuse_warm=True):
        pass
    platform.confirm_delete = False
    with pytest.raises(TimeoutError):
        async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
            pytest.fail("replacement was admitted")
    assert len(platform.created) == 1
    assert runtime._admission._semaphore._value == 1
    assert runtime.has_pending_ownership
    platform.confirm_delete = True
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_queue_timeout_never_closes_active_sandbox(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_acquisition_timeout_seconds = 0.03
    workspace_id = uuid4()
    async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True):
        with pytest.raises(TimeoutError):
            async with runtime.open_workspace_sandbox(workspace_id, purpose="checkpoint", reuse_warm=True):
                pytest.fail("overlapping operation was admitted")
        assert not platform.deleted
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_operation_groups_serialize_and_share_warm_sandbox(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    workspace_id = uuid4()
    entered = asyncio.Event()
    finish = asyncio.Event()
    seen: list[str] = []

    async def read() -> None:
        async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True) as sandbox:
            seen.append(sandbox.id)
            entered.set()
            await finish.wait()

    async def checkpoint() -> None:
        async with runtime.open_workspace_sandbox(workspace_id, purpose="checkpoint", reuse_warm=True) as sandbox:
            seen.append(sandbox.id)

    first = asyncio.create_task(read())
    await entered.wait()
    second = asyncio.create_task(checkpoint())
    await asyncio.sleep(0)
    assert seen == ["io-0"]
    assert runtime._workspace_io_contexts == 2
    finish.set()
    await asyncio.gather(first, second)
    assert seen == ["io-0", "io-0"]
    assert len(platform.created) == 1
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_acquisition_bound_does_not_limit_active_work(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_acquisition_timeout_seconds = 0.03
    async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
        await asyncio.sleep(0.05)
        assert not platform.deleted
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_capacity_one_does_not_retain_idle_permit(workspace_runtime) -> None:
    runtime, platform = workspace_runtime
    runtime._admission = DaytonaAdmission(max_active_leases=1)
    async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
        assert runtime._admission._semaphore._value == 0
    assert not runtime._warm_workspace_io_sandboxes
    assert platform.deleted == ["io-0"]
    assert runtime._admission._semaphore._value == 1
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_workspace_acquisition_honors_calling_action_deadline(workspace_runtime, expired: bool) -> None:
    from fleet_rlm.rlm.budget import host_action_deadline

    runtime, platform = workspace_runtime
    loop = asyncio.get_running_loop()
    permits = [await runtime._admission.acquire(deadline=loop.time() + 1, host_io=True) for _ in range(2)]
    started = loop.time()
    with (
        host_action_deadline(started + (-1 if expired else 0.03)),
        pytest.raises((TimeoutError, DaytonaAdmissionTimeoutError)),
    ):
        async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
            pytest.fail("expired acquisition was admitted")
    assert loop.time() - started < 0.5
    assert not platform.created
    assert runtime._workspace_io_contexts == 0
    for permit in permits:
        permit.release()
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("reused", [False, True])
async def test_workspace_preparation_timeout_retains_cleanup(workspace_runtime, monkeypatch, reused: bool) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_acquisition_timeout_seconds = 0.03
    workspace_id = uuid4()

    async def blocked(*_args: object) -> None:
        await asyncio.Event().wait()

    if reused:
        async with runtime.open_workspace_sandbox(workspace_id, purpose="prime", reuse_warm=True) as sandbox:
            pass
        sandbox.refresh_data.side_effect = blocked
    else:
        monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", blocked)
    with pytest.raises(TimeoutError):
        async with runtime.open_workspace_sandbox(workspace_id, purpose="read", reuse_warm=True):
            pytest.fail("unfinished preparation was admitted")
    assert not runtime._warm_workspace_io_sandboxes
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert not platform.live
    assert runtime._admission._semaphore._value == 2
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_late_create_blocks_replacement(workspace_runtime, monkeypatch) -> None:
    runtime, platform = workspace_runtime
    runtime._workspace_io_acquisition_timeout_seconds = 0.03
    original_create = runtime_module._create_daytona_sandbox
    finish = asyncio.Event()
    creates = 0

    async def blocked_create(*args: object, **kwargs: object) -> object:
        nonlocal creates
        creates += 1
        await finish.wait()
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(runtime_module, "_create_daytona_sandbox", blocked_create)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
                pytest.fail("late creation was admitted")
    assert creates == 1
    assert runtime.has_pending_ownership
    assert runtime._admission._semaphore._value == 1
    finish.set()
    assert await runtime.wait_pending_cleanup(timeout=2)
    assert platform.deleted == ["io-0"]
    async with runtime.open_workspace_sandbox(uuid4(), purpose="retry", reuse_warm=True):
        pass
    assert creates == 2
    assert await runtime.aclose(drain_seconds=1)


@pytest.mark.asyncio
async def test_workspace_shutdown_drains_preparation(workspace_runtime, monkeypatch) -> None:
    runtime, platform = workspace_runtime
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def layout(*_args: object) -> None:
        entered.set()
        await finish.wait()

    monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", layout)

    async def prepare() -> None:
        async with runtime.open_workspace_sandbox(uuid4(), purpose="read", reuse_warm=True):
            pytest.fail("preparation published after shutdown")

    task = asyncio.create_task(prepare())
    await entered.wait()
    assert not await runtime.aclose(drain_seconds=0.01)
    assert not platform.deleted
    finish.set()
    with pytest.raises(RuntimeError, match="closed during Workspace I/O preparation"):
        await task
    assert await runtime.aclose(drain_seconds=1)
    assert platform.deleted == ["io-0"]


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
async def test_workspace_io_warm_lease_reuses_resident_sandbox_and_closes_on_aclose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = SimpleNamespace(id="io-warm")

    async def refresh_data() -> None:
        return None

    sandbox.refresh_data = refresh_data
    create_count = 0
    deleted: list[str] = []

    class Platform:
        async def create(self, **_kwargs: object) -> object:
            nonlocal create_count
            create_count += 1
            return sandbox

        async def delete(self, sandbox_id: str) -> None:
            deleted.append(sandbox_id)

        async def get(self, _sandbox_id: str) -> object | None:
            return None if deleted else sandbox

    async def confirm(**_kwargs: object) -> AbsenceConfirmation | AbsenceTimeout:
        return AbsenceConfirmation(sandbox.id, ("absent",), 0.0)

    async def volume_id(*_args: object) -> str:
        return "volume"

    monkeypatch.setattr(runtime_module, "confirm_absence", confirm)
    monkeypatch.setattr(runtime_module, "get_or_create_volume_id", volume_id)
    monkeypatch.setattr(
        runtime_module, "_create_daytona_sandbox", lambda _platform, _expected, **_kwargs: platform.create(**_kwargs)
    )

    async def layout(*_args: object) -> None:
        return None

    monkeypatch.setattr(runtime_module, "sandbox_state", lambda _sandbox: "running")
    monkeypatch.setattr(runtime_module, "ensure_shared_volume_layout", layout)
    monkeypatch.setattr(runtime_module, "_expected_workspace_mount", lambda *_args: object())
    monkeypatch.setattr(runtime_module, "verify_sandbox_workspace_mount", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "verify_sandbox_spec", lambda *_args: None)

    admission = DaytonaAdmission(max_active_leases=2)
    platform = Platform()
    runtime = make_daytona_runtime(
        platform=platform,
        volume_client=object(),
        volume_config=SimpleNamespace(paths=object),
        admission=admission,
    )
    ws_id = uuid4()

    # First call creates the warm sandbox
    async with runtime.open_workspace_sandbox(ws_id, purpose="mem-read", reuse_warm=True) as acquired1:
        assert acquired1 is sandbox
        assert create_count == 1
        assert not deleted

    # Not deleted on exit of contextmanager
    assert not deleted
    assert ws_id in runtime._warm_workspace_io_sandboxes

    # Second call reuses the existing warm sandbox without creating a new one
    async with runtime.open_workspace_sandbox(ws_id, purpose="mem-append", reuse_warm=True) as acquired2:
        assert acquired2 is sandbox
        assert create_count == 1
        assert not deleted

    # Body exception during warm reuse propagates cleanly without generator athrow error
    with pytest.raises(FileNotFoundError, match=r"task\.json"):
        async with runtime.open_workspace_sandbox(ws_id, purpose="read-task", reuse_warm=True) as acquired3:
            assert acquired3 is sandbox
            raise FileNotFoundError("task.json")

    # Calling aclose() cleanly terminates and confirms deletion of the warm sandbox
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 1)
    assert sandbox.id in deleted
    assert not runtime._warm_workspace_io_sandboxes


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


# --- Process-wide Daytona Interpreter Lease admission behavior --------
def test_admission_rejects_more_than_eight_direct_leases() -> None:
    with pytest.raises(ValueError, match="at most 8"):
        DaytonaAdmission(max_active_leases=9)


@pytest.mark.asyncio
async def test_execution_reserves_one_of_eight_leases_for_host_io() -> None:
    admission = DaytonaAdmission(max_active_leases=8)
    deadline = asyncio.get_running_loop().time() + 10
    permits = [await admission.acquire(deadline=deadline) for _ in range(7)]
    io_permit = await admission.acquire(deadline=deadline, host_io=True)

    ninth = asyncio.create_task(admission.acquire(deadline=deadline))
    await asyncio.sleep(0)
    assert not ninth.done()

    permits[0].release()
    ninth_permit = await asyncio.wait_for(ninth, timeout=1)
    ninth_permit.release()
    for permit in permits[1:]:
        permit.release()
    io_permit.release()


@pytest.mark.asyncio
async def test_cancelled_waiter_restores_capacity() -> None:
    admission = DaytonaAdmission(max_active_leases=1)
    deadline = asyncio.get_running_loop().time() + 10
    held = await admission.acquire(deadline=deadline)
    waiter = asyncio.create_task(admission.acquire(deadline=deadline))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    held.release()

    replacement = await admission.acquire(deadline=deadline)
    replacement.release()


@pytest.mark.asyncio
async def test_deadline_exhaustion_does_not_consume_capacity() -> None:
    admission = DaytonaAdmission(max_active_leases=1)
    loop = asyncio.get_running_loop()
    held = await admission.acquire(deadline=loop.time() + 10)

    with pytest.raises(DaytonaAdmissionTimeoutError, match="Daytona admission unavailable"):
        await admission.acquire(deadline=loop.time())

    held.release()
    available = await admission.acquire(deadline=loop.time() + 10)
    available.release()


# --- Phase 2 Preservation Seam Contracts ---
def _closable_lease(*, sandbox_id: str = "sandbox-1") -> object:
    class Interpreter:
        closed = False

        def shutdown(self, **_kwargs: object) -> None:
            self.closed = True

    return InterpreterLease(
        sandbox_id=sandbox_id,
        interpreter_id=f"interpreter-{sandbox_id}",
        volume_id="volume-1",
        mount_path="/workspace",
        interpreter=Interpreter(),
        sandbox=type("Sandbox", (), {"id": sandbox_id})(),
    )


@pytest.mark.asyncio
async def test_sequential_root_reuse_returns_same_lease() -> None:
    from fleet_rlm.daytona.runtime import DaytonaSessionRecord, RootSessionSpec, SessionCleanupState

    creates = 0

    async def acquire(_request: object, **_kwargs: object) -> object:
        nonlocal creates
        creates += 1
        return _closable_lease()

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first = await runtime.acquire_root_session(spec)
    second = await runtime.acquire_root_session(spec)

    assert second is first
    assert creates == 1
    record = runtime.session_record(spec.workspace_id, spec.session_id)
    assert isinstance(record, DaytonaSessionRecord)
    assert record.cleanup_state is SessionCleanupState.ACTIVE
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_two_concurrent_sessions_acquire_independently() -> None:
    from fleet_rlm.daytona.runtime import RootSessionSpec

    started = asyncio.Event()
    release_second = asyncio.Event()
    calls: list[str] = []

    async def acquire(request: LeaseRequest, **_kwargs: object) -> object:
        calls.append(str(request.session_id))
        if len(calls) == 1:
            started.set()
            await release_second.wait()
        return _closable_lease(sandbox_id=f"sandbox-{len(calls)}")

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    first_spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())
    second_spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first_task = asyncio.create_task(runtime.acquire_root_session(first_spec))
    await started.wait()
    second = await asyncio.wait_for(runtime.acquire_root_session(second_spec), timeout=5.0)
    release_second.set()
    first = await asyncio.wait_for(first_task, timeout=5.0)

    assert first is not second
    assert len(runtime.roots) == 2
    assert await runtime.aclose() is True


@pytest.mark.asyncio
async def test_failed_root_creation_keeps_late_ownership_visible() -> None:
    from fleet_rlm.daytona.runtime import RootSessionSpec

    landed = asyncio.Event()
    lease = _closable_lease()

    async def acquire(_request: object, **_kwargs: object) -> object:
        await landed.wait()
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(
        workspace_id=uuid4(),
        session_id=uuid4(),
        deadline=asyncio.get_running_loop().time() + 0.05,
    )

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await runtime.acquire_root_session(spec)

    assert runtime.has_pending_ownership
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 0.01) is False
    assert not lease.closed
    landed.set()
    assert await runtime.aclose(deadline=asyncio.get_running_loop().time() + 5.0) is True
    assert lease.closed
    assert not runtime.has_pending_ownership


@pytest.mark.asyncio
async def test_cancelled_acquisition_does_not_publish_a_root() -> None:
    from fleet_rlm.daytona.runtime import RootSessionSpec

    started = asyncio.Event()
    landed = asyncio.Event()
    lease = _closable_lease()

    async def acquire(_request: object, **_kwargs: object) -> object:
        started.set()
        await landed.wait()
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    task = asyncio.create_task(runtime.acquire_root_session(spec))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.roots == ()
    assert runtime.session_record(spec.workspace_id, spec.session_id) is None
    assert runtime.has_pending_ownership
    landed.set()
    assert await runtime.aclose() is True
    assert lease.closed


@pytest.mark.asyncio
async def test_tainted_root_rotates_on_next_acquisition() -> None:
    from fleet_rlm.daytona.runtime import RootSessionSpec

    leases = [_closable_lease(sandbox_id="sandbox-old"), _closable_lease(sandbox_id="sandbox-new")]
    calls = 0

    async def acquire(_request: object, **_kwargs: object) -> object:
        nonlocal calls
        lease = leases[calls]
        calls += 1
        return lease

    runtime = make_daytona_runtime()
    runtime.acquire = acquire  # type: ignore[method-assign]
    spec = RootSessionSpec(workspace_id=uuid4(), session_id=uuid4())

    first = await runtime.acquire_root_session(spec)
    assert first.sandbox_id == "sandbox-old"
    runtime.mark_root_tainted(spec.workspace_id, spec.session_id)
    second = await runtime.acquire_root_session(spec)

    assert second is not first
    assert second.sandbox_id == "sandbox-new"
    assert calls == 2
    assert await runtime.aclose() is True


def test_broker_transport_limits_preserved() -> None:
    from fleet_rlm.daytona.broker import _MAX_REQUEST_BYTES

    assert _MAX_REQUEST_BYTES == 2 * 1024 * 1024


# --- Run Environment History and Attachment Routing ---
def _make_claim(*, history_messages=()):
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput
    from fleet_rlm.sessions.run_state import ClaimedRun, _RunClaimToken

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        TurnAccess(uuid4(), uuid4()),
        TurnInput("current"),
        SessionHistory(messages=history_messages),
        not_cancelled,
        _RunClaimToken(uuid4(), base_checkpoint_version=2),
    )


def test_turn_preparation_exposes_committed_session_history_builder() -> None:
    from fleet_rlm.sessions import history as history_transport

    assert callable(history_transport.committed_history_for_claim)


def test_daytona_helper_returns_committed_session_history_not_dspy_history() -> None:
    import dspy

    from fleet_rlm.sessions.history import CommittedSessionHistory, committed_history_for_claim
    from fleet_rlm.sessions.models import HistoryMessage

    claim = _make_claim(
        history_messages=(
            HistoryMessage("user", "earlier user request"),
            HistoryMessage("assistant", "earlier assistant answer"),
        )
    )
    history = committed_history_for_claim(claim)

    assert type(history) is CommittedSessionHistory
    assert not isinstance(history, dspy.History)


@pytest.mark.asyncio
async def test_run_attachment_copy_uses_local_scratch_with_parent_directories() -> None:
    from pathlib import PurePosixPath

    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.paths import VolumePaths
    from fleet_rlm.workspace.host_io import DaytonaRunStorage
    from tests.support.workspace_storage import daytona_host_io_for_test_sandbox

    class Fs:
        def __init__(self) -> None:
            self.directories = {"/tmp/fleet"}
            self.files: dict[str, bytes] = {}

        async def create_folder(self, path: str, _mode: str) -> None:
            assert str(PurePosixPath(path).parent) in self.directories
            self.directories.add(path)

        async def upload_file(self, data: bytes, path: str) -> None:
            self.files[path] = bytes(data)

        async def download_file(self, path: str) -> bytes:
            return self.files[path]

        async def delete_file(self, path: str) -> None:
            self.files.pop(path)

    run_id = uuid4()
    attachment_id = uuid4()
    fs = Fs()
    fs.directories.add(f"/tmp/fleet/{run_id}")
    sandbox = SimpleNamespace(fs=fs)
    paths = VolumePaths.from_mount("/volume")
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    host_io = daytona_host_io_for_test_sandbox(
        sandbox,
        workspace_id=uuid4(),
        dispatcher=dispatcher,
        volume_root=str(paths.mount_path),
        max_file_bytes=1_000_000,
    )
    sink = DaytonaRunStorage(
        sandbox,
        dispatcher=dispatcher,
        paths=paths,
        host_io=host_io,
        run_id=run_id,
    )
    path = f"/tmp/fleet/{run_id}/attachments/{attachment_id}/notes.txt"

    await sink.write_private(path, b"body")
    assert await sink.read(path, max_bytes=4) == b"body"
    assert f"/tmp/fleet/{run_id}/attachments/{attachment_id}" in fs.directories
    await sink.remove_private(path)
    assert path not in fs.files


@pytest.mark.asyncio
async def test_run_sink_routes_sync_storage_to_host_or_private_scratch() -> None:
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.paths import VolumePaths
    from fleet_rlm.workspace.host_io import DaytonaRunStorage

    class SandboxFs:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        async def upload_file(self, data: bytes, path: str) -> None:
            self.files[path] = bytes(data)

        async def download_file(self, path: str) -> bytes:
            return self.files[path]

        async def delete_file(self, path: str) -> None:
            self.files.pop(path, None)

    class HostFs:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        def write_bytes(self, path: str, data: bytes, *, max_bytes: int | None = None) -> None:
            assert max_bytes is None or len(data) <= max_bytes
            self.files[path] = bytes(data)

        def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
            value = self.files[path]
            return value if max_bytes is None else value[:max_bytes]

        def exists(self, path: str) -> bool:
            return path in self.files

        def remove_bytes(self, path: str) -> None:
            self.files.pop(path, None)

        def remove(self, path: str) -> None:
            self.remove_bytes(path)

    run_id = uuid4()
    sandbox_fs = SandboxFs()
    host_fs = HostFs()
    dispatcher = SyncBridgeDispatcher()
    dispatcher.set_loop(asyncio.get_running_loop())
    sink = DaytonaRunStorage(
        SimpleNamespace(fs=sandbox_fs),
        dispatcher=dispatcher,
        paths=VolumePaths.from_mount("/volume"),
        host_io=SimpleNamespace(volume_fs=host_fs),
        run_id=run_id,
    )
    scratch_path = f"/tmp/fleet/{run_id}/attachments/source.txt"
    host_path = "/volume/sessions/session/runs/run/result.json"

    await asyncio.to_thread(sink.volume_fs.write_bytes, scratch_path, b"scratch")
    await asyncio.to_thread(sink.volume_fs.write_bytes, host_path, b"host")

    assert scratch_path in sandbox_fs.files
    assert host_fs.files == {host_path: b"host"}
    assert await asyncio.to_thread(sink.volume_fs.read_bytes, scratch_path) == b"scratch"
    assert await asyncio.to_thread(sink.volume_fs.read_bytes, host_path) == b"host"


# --- Live Proof Cleanup Contracts ---
class _LivePlatformDouble:
    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def get(self, sandbox_id: str) -> SimpleNamespace:
        self.get_calls.append(sandbox_id)
        return SimpleNamespace(id=sandbox_id)

    async def delete(self, sandbox: SimpleNamespace) -> None:
        self.delete_calls.append(str(sandbox.id))


class _LiveVolumeClientDouble:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, bool]] = []
        self.delete_calls: list[object] = []

    async def get(self, name: str, *, create: bool) -> SimpleNamespace:
        self.get_calls.append((name, create))
        return SimpleNamespace(name=name)

    async def delete(self, volume: SimpleNamespace) -> None:
        self.delete_calls.append(volume)


def test_strict_cleanup_awaits_provider_operations_before_returning() -> None:
    from tests.live._cleanup import _strict_cleanup

    platform = _LivePlatformDouble()
    volume = _LiveVolumeClientDouble()
    resources = SimpleNamespace(
        _tracked_sandbox_ids=["sandbox-b", "sandbox-a"],
        _platform=platform,
        _client=SimpleNamespace(volume=volume),
    )

    failures = asyncio.run(_strict_cleanup(resources, "phase1-volume"))

    assert failures == ()
    assert platform.get_calls == ["sandbox-a", "sandbox-b"]
    assert platform.delete_calls == ["sandbox-a", "sandbox-b"]
    assert volume.get_calls == [("phase1-volume", False)]
    assert len(volume.delete_calls) == 1
    assert resources._tracked_sandbox_ids == []


@pytest.mark.parametrize("failed_resource", ["sandbox", "volume"])
def test_cleanup_failure_is_reported_and_other_resources_still_settle(monkeypatch, failed_resource) -> None:
    from tests.live import _cleanup
    from tests.live._cleanup import _strict_cleanup

    monkeypatch.setattr(_cleanup, "_CLEANUP_RETRY_DELAYS", ())
    platform, volume = _LivePlatformDouble(), _LiveVolumeClientDouble()
    resources = SimpleNamespace(
        _tracked_sandbox_ids=["sandbox-a"], _platform=platform, _client=SimpleNamespace(volume=volume)
    )

    async def fail(_resource):
        raise RuntimeError("private provider diagnostic must not enter the receipt")

    monkeypatch.setattr(platform if failed_resource == "sandbox" else volume, "delete", fail)

    failures = asyncio.run(_strict_cleanup(resources, "owned-volume"))

    assert failures == (failed_resource,)
    assert resources._tracked_sandbox_ids == []
    if failed_resource == "sandbox":
        assert len(volume.delete_calls) == 1
    else:
        assert platform.delete_calls == ["sandbox-a"]


def test_mvp_cleanup_skips_configured_shared_volume() -> None:
    from tests.live._mvp_support import _strict_cleanup as _mvp_strict_cleanup

    platform = _LivePlatformDouble()
    volume = _LiveVolumeClientDouble()
    resources = SimpleNamespace(
        _tracked_sandbox_ids=["sandbox-b", "sandbox-a"],
        _platform=platform,
        _client=SimpleNamespace(volume=volume),
    )

    failures = asyncio.run(_mvp_strict_cleanup(resources, set(), "fleet-volume"))

    assert failures == ()
    assert platform.get_calls == ["sandbox-a", "sandbox-b"]
    assert platform.delete_calls == ["sandbox-a", "sandbox-b"]
    assert volume.get_calls == []
    assert volume.delete_calls == []
    assert resources._tracked_sandbox_ids == []


def test_mvp_cleanup_deletes_ephemeral_proof_volume() -> None:
    from tests.live._mvp_support import _strict_cleanup as _mvp_strict_cleanup

    platform = _LivePlatformDouble()
    volume = _LiveVolumeClientDouble()
    resources = SimpleNamespace(
        _tracked_sandbox_ids=["sandbox-a"], _platform=platform, _client=SimpleNamespace(volume=volume)
    )
    name = "fleet-rlm-live-mvp-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    failures = asyncio.run(_mvp_strict_cleanup(resources, set(), name))

    assert failures == ()
    assert platform.delete_calls == ["sandbox-a"]
    assert volume.get_calls == [(name, False)]
    assert len(volume.delete_calls) == 1
    assert resources._tracked_sandbox_ids == []
