"""QRE-155 contracts: typed Sandbox Lease cleanup receipts and seam semantics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from fleet_rlm.daytona.sandbox_lease import (
    OwnedAcquisition,
    SandboxLease,
    SandboxLeasePolicy,
    SandboxLeaseReceipt,
    acquire_owned_lease,
)
from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionPermit


@dataclass
class _Sandbox:
    id: str
    state: str = "started"


class _ScriptedPlatform:
    """Provider double: delete records requests; get walks a state script."""

    def __init__(self, states: list[str | None], *, delete_error: BaseException | None = None) -> None:
        self.states = list(states)
        self.delete_error = delete_error
        self.deletes: list[str] = []
        self.stops: list[str] = []
        self.probes = 0

    async def delete(self, sandbox_id: str) -> None:
        self.deletes.append(sandbox_id)
        if self.delete_error is not None:
            raise self.delete_error

    async def get(self, sandbox_id: str) -> _Sandbox | None:
        self.probes += 1
        if not self.states:
            return None
        state = self.states.pop(0)
        if state is None:
            return None
        return _Sandbox(id=sandbox_id, state=state)

    async def stop(self, sandbox_id: str, *, timeout: float = 60, force: bool = False) -> None:
        assert timeout > 0
        self.stops.append(f"{sandbox_id}:{force}")


class _FakeBroker:
    def __init__(self) -> None:
        self.stopped_strict: list[bool] = []

    def stop(self, strict: bool = False) -> None:
        self.stopped_strict.append(strict)


class _FakeBackend:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeInterpreter:
    """Minimal stand-in with the public shutdown contract of DaytonaCodeInterpreter."""

    def __init__(self, *, fail: bool = False) -> None:
        self._http_broker = _FakeBroker()
        self._backend = _FakeBackend()
        self._shutdown = False
        self._fail = fail

    def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._http_broker.stop(strict=strict_broker_cleanup)
        self._backend.close()
        if self._fail:
            raise RuntimeError("broker stop failed")


async def _permit() -> tuple[DaytonaAdmission, DaytonaAdmissionPermit]:
    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 5)
    return admission, permit


@pytest.mark.asyncio
async def test_ephemeral_lease_confirms_absence_before_admission_release() -> None:
    platform = _ScriptedPlatform(["destroying", None])
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=_Sandbox(id="sb-1"),
        sandbox_id="sb-1",
        platform=platform,
        permit=permit,
        policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01, confirm_timeout_s=5.0),
    )
    receipt = await lease.aclose()
    assert isinstance(receipt, SandboxLeaseReceipt)
    assert receipt.provider.confirmed_absent is True
    assert receipt.provider.plateau == ("destroying", "not_found")
    assert receipt.admission.released is True
    assert receipt.admission.released_after == "confirmed_cleanup"
    assert permit._released is True  # released strictly after confirmation
    assert platform.probes == 2
    assert receipt.interpreter.status == "not_present"
    assert receipt.clean is True


@pytest.mark.asyncio
async def test_close_pipeline_order_purge_before_delete_before_release() -> None:
    platform = _ScriptedPlatform([None])
    _, permit = await _permit()
    order: list[str] = []

    class _OrderingInterpreter(_FakeInterpreter):
        def shutdown(self, *, strict_broker_cleanup: bool = False) -> None:
            order.append("interpreter")
            super().shutdown(strict_broker_cleanup=strict_broker_cleanup)

    async def purge(_sandbox: Any) -> None:
        order.append("purge")

    platform_orig_delete = platform.delete

    async def recorder(sid: str) -> None:
        order.append("delete")
        await platform_orig_delete(sid)

    platform.delete = recorder  # type: ignore[method-assign]
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=_Sandbox(id="sb-2"),
        sandbox_id="sb-2",
        platform=platform,
        permit=permit,
        interpreter=_OrderingInterpreter(),
        purge=purge,
        policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01),
    )
    receipt = await lease.aclose()
    order.append("released" if permit._released else "not-released")
    assert order == ["interpreter", "purge", "delete", "released"]
    assert receipt.interpreter.status == "clean"
    assert receipt.interpreter.broker == "stopped"
    assert receipt.clean


@pytest.mark.asyncio
async def test_unconfirmed_absence_is_quarantine_failure_with_receipt() -> None:
    platform = _ScriptedPlatform(["destroying"] * 100)
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=None,
        sandbox_id="sb-3",
        platform=platform,
        permit=permit,
        policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01, confirm_timeout_s=0.05),
    )
    receipt = await lease.aclose()
    assert receipt.provider.confirmed_absent is False
    assert receipt.quarantine.quarantined is True
    assert receipt.quarantine.error is not None
    assert receipt.admission.released_after == "quarantine_failure"
    assert permit._released is True  # quarantine releases exactly once, never silently
    assert receipt.first_error is not None
    assert receipt.clean is False


@pytest.mark.asyncio
async def test_delete_request_error_still_confirms_and_reports_both() -> None:
    platform = _ScriptedPlatform([None], delete_error=RuntimeError("provider 503"))
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=None,
        sandbox_id="sb-4",
        platform=platform,
        permit=permit,
        policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01),
    )
    receipt = await lease.aclose()
    assert "provider 503" in str(receipt.provider.error)
    assert receipt.provider.confirmed_absent is True  # probe still ran
    assert receipt.admission.released is True
    assert permit._released is True


@pytest.mark.asyncio
async def test_repeated_close_is_idempotent_and_returns_same_receipt() -> None:
    platform = _ScriptedPlatform([None])
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=_Sandbox(id="sb-5"),
        sandbox_id="sb-5",
        platform=platform,
        permit=permit,
        interpreter=_FakeInterpreter(),
    )
    first = await lease.aclose()
    second = await lease.aclose()
    third = lease.close()  # sync variant after async close
    assert first is second is third
    assert platform.deletes == ["sb-5"]  # provider cleanup happened exactly once
    assert lease.closed is True


@pytest.mark.asyncio
async def test_lifecycle_policies_have_explicit_kinds() -> None:
    """Every lifecycle kind closes with its own explicit provider semantics."""
    platform = _ScriptedPlatform([None])
    cases: list[tuple[SandboxLeasePolicy, str | None]] = [
        (SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01), "delete"),
        (SandboxLeasePolicy(kind="volume_io", confirm_poll_interval_s=0.01), "delete"),
        (SandboxLeasePolicy(kind="retained_session", provider_action="stop"), "stop"),
        (SandboxLeasePolicy(kind="recovery_fence", provider_action="stop", stop_force=True), "stop"),
    ]
    for policy, expected_action in cases:
        _, permit = await _permit()
        lease = SandboxLease(
            kind=policy.kind,
            sandbox=_Sandbox(id=f"sb-{policy.kind}"),
            sandbox_id=f"sb-{policy.kind}",
            platform=platform,
            permit=permit,
            policy=policy,
        )
        receipt = await lease.aclose()
        assert receipt.provider.action == expected_action
        assert receipt.kind == policy.kind


@pytest.mark.asyncio
async def test_retained_session_policy_never_deletes() -> None:
    platform = _ScriptedPlatform([None])
    _, permit = await _permit()
    lease = SandboxLease(
        kind="retained_session",
        sandbox=_Sandbox(id="sb-keep"),
        sandbox_id="sb-keep",
        platform=platform,
        permit=permit,
        policy=SandboxLeasePolicy(kind="retained_session", provider_action="stop"),
    )
    await lease.aclose()
    assert platform.deletes == []
    assert platform.stops == ["sb-keep:False"]


@pytest.mark.asyncio
async def test_close_leaves_no_pending_provider_tasks() -> None:
    platform = _ScriptedPlatform(nil_states := [None])
    platform.states = nil_states
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=_Sandbox(id="sb-6"),
        sandbox_id="sb-6",
        platform=platform,
        permit=permit,
        interpreter=_FakeInterpreter(),
        policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01),
    )
    before = {t for t in asyncio.all_tasks() if not t.done()}
    await lease.aclose()
    after = {t for t in asyncio.all_tasks() if not t.done()}
    assert after <= before  # the lease leaks no task


@pytest.mark.asyncio
async def test_interpreter_failure_surfaces_on_receipt_without_raising() -> None:
    platform = _ScriptedPlatform([None])
    _, permit = await _permit()
    lease = SandboxLease(
        kind="recursive_child",
        sandbox=_Sandbox(id="sb-7"),
        sandbox_id="sb-7",
        platform=platform,
        permit=permit,
        interpreter=_FakeInterpreter(fail=True),
    )
    receipt = await lease.aclose()
    assert receipt.interpreter.status == "failed"
    assert receipt.interpreter.broker == "failed"
    assert "broker stop failed" in str(receipt.first_error)
    assert receipt.clean is False


@pytest.mark.asyncio
async def test_owned_acquisition_settles_and_closes_lease() -> None:
    """Late acquisition ownership: adopted future results get closed, never stranded."""
    import concurrent.futures
    import threading

    loop = asyncio.get_running_loop()
    platform = _ScriptedPlatform([None])
    _, permit = await _permit()

    async def acquire() -> SandboxLease:
        return SandboxLease(
            kind="recursive_child",
            sandbox=_Sandbox(id="sb-8"),
            sandbox_id="sb-8",
            platform=platform,
            permit=permit,
            policy=SandboxLeasePolicy(kind="recursive_child", confirm_poll_interval_s=0.01),
        )

    # The seam is thread-facing: post from a worker thread like DSPy workers do.
    holder: dict[str, Any] = {}

    def post() -> None:
        holder["future"] = acquire_owned_lease(loop=loop, acquire=acquire)

    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    future: concurrent.futures.Future[SandboxLease] = holder["future"]
    # result() blocks until the owner loop settles the coroutine; run it off-loop.
    lease = await asyncio.to_thread(future.result, 5)

    acquisition = OwnedAcquisition(loop=loop, close_lease=lambda _: asyncio.sleep(0))
    assert acquisition.owned_futures == []
    adopted: concurrent.futures.Future[Any] = concurrent.futures.Future()
    adopted.set_result(lease)
    # settle_adopted closes synchronously (lease.close uses asyncio.run); keep
    # it on a worker thread like its production callers.
    receipt = await asyncio.to_thread(acquisition.settle_adopted, adopted)
    assert isinstance(receipt, SandboxLeaseReceipt)
    assert receipt.provider.confirmed_absent is True
    assert permit._released is True
