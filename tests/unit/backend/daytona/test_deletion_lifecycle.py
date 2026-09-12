"""Behavior contracts for deletion lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from fleet_rlm.daytona.lifecycle import (
    AbsenceConfirmation,
    AbsenceProbeError,
    AbsenceTimeout,
    classify_deletion_phase,
    confirm_absence,
)
from fleet_rlm.daytona.recursive_child_runtime import cleanup_child_runtime_async
from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionPermit
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError


@dataclass
class _FakeSandbox:
    """Minimal provider object exposing a mutable raw state."""

    state: str


@dataclass
class _FakeProvider:
    """Scriptable provider: delete() only marks the request accepted.

    The caller transitions states explicitly to model provider teardown; the
    probe observes whatever the provider currently reports, returning ``None``
    once the Sandbox is purged (explicit not-found).
    """

    seen_deletes: list[str] = field(default_factory=list)
    target: _FakeSandbox | None = None
    deleted: bool = False

    async def delete(self, sandbox_id: str) -> None:
        self.seen_deletes.append(sandbox_id)
        # Acceptance only: no state transition is implied here.

    async def get(self, _sandbox_id: str) -> Any | None:
        if self.deleted:
            return None
        return self.target


class _StepClock:
    """Deterministic monotonic clock; each tick advances by a fixed step."""

    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self._step
        return value

    async def sleep(self, _seconds: float) -> None:
        self.now += self._step


@pytest.mark.parametrize(
    ("raw", "phase"),
    [
        ("started", "requested"),
        ("stopped", "requested"),
        ("archived", "requested"),
        ("creating", "requested"),
        ("unknown", "requested"),
        ("", "requested"),
        ("destroying", "deleting"),
        ("deleting", "deleting"),
        ("archiving", "deleting"),
        ("stopping", "deleting"),
        ("destroyed", "absent"),
        ("deleted", "absent"),
        ("error", "failed"),
        ("build_failed", "failed"),
    ],
)
def test_classify_deletion_phase_maps_raw_states(raw: str, phase: str) -> None:
    assert classify_deletion_phase(raw) == phase


@pytest.mark.asyncio
async def test_delete_request_acceptance_is_not_absence() -> None:
    provider = _FakeProvider(target=_FakeSandbox(state="started"))
    await provider.delete("sb-1")
    assert provider.seen_deletes == ["sb-1"]
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-1",
        timeout_s=5.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceTimeout)
    assert outcome.absent is False
    assert outcome.last_state == "started"


@pytest.mark.asyncio
async def test_requested_then_deleting_then_absent_and_purged() -> None:
    """The three observable phases are distinct and recorded in order."""
    provider = _FakeProvider(target=_FakeSandbox(state="started"))
    clock = _StepClock()

    async def scripted_probe(sandbox_id: str) -> Any | None:
        # Provider timeline: request accepted -> destroying -> purged.
        calls = scripted_probe.calls
        scripted_probe.calls = calls + 1
        if calls == 0:
            provider.target = _FakeSandbox(state="started")
        elif calls == 1:
            provider.target = _FakeSandbox(state="destroying")
        else:
            provider.deleted = True
        return await provider.get(sandbox_id)

    scripted_probe.calls = 0

    outcome = await confirm_absence(
        probe=scripted_probe,
        sandbox_id="sb-2",
        timeout_s=30.0,
        clock=clock,
        sleep=clock.sleep,
    )
    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.absent is True
    assert outcome.observations == ("started", "destroying", "not_found")


@pytest.mark.asyncio
async def test_terminal_destroyed_state_confirms_without_purge() -> None:
    provider = _FakeProvider(target=_FakeSandbox(state="destroyed"))
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-3",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceConfirmation)
    assert outcome.observations == ("destroyed",)


@pytest.mark.asyncio
async def test_provider_error_state_is_classified_failure_not_absence() -> None:
    provider = _FakeProvider(target=_FakeSandbox(state="error"))
    outcome = await confirm_absence(
        probe=provider.get,
        sandbox_id="sb-4",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceProbeError)
    assert outcome.absent is False
    assert "provider error state" in outcome.error


@pytest.mark.asyncio
async def test_probe_error_is_classified_not_silent() -> None:
    async def failing_probe(_sandbox_id: str) -> Any | None:
        raise RuntimeError("provider 503")

    outcome = await confirm_absence(
        probe=failing_probe,
        sandbox_id="sb-5",
        timeout_s=30.0,
        clock=_StepClock(),
        sleep=_StepClock().sleep,
    )
    assert isinstance(outcome, AbsenceProbeError)
    assert "provider 503" in outcome.error


@pytest.mark.asyncio
async def test_cancellation_propagates_instead_of_classifying() -> None:
    async def cancelled_probe(_sandbox_id: str) -> Any | None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await confirm_absence(
            probe=cancelled_probe,
            sandbox_id="sb-6",
            timeout_s=30.0,
            clock=_StepClock(),
            sleep=_StepClock().sleep,
        )


@dataclass
class _Sandbox:
    id: str
    state: str = "started"


class _FsStub:
    async def list_files(self, _root: str, *, depth: int | None) -> list[Any]:
        assert depth is None
        return []

    async def delete_file(self, _path: str, *, recursive: bool = False) -> None:
        del recursive
        raise AssertionError("no files should be purged in this test")


class _ScriptedPlatform:
    """Provider double: delete() marks requested; get() walks a state script."""

    def __init__(self, states: list[str | None], *, delete_error: BaseException | None = None) -> None:
        self.states = list(states)
        self.delete_error = delete_error
        self.deletes: list[str] = []
        self.probes: int = 0

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


async def _take_permit() -> tuple[DaytonaAdmission, DaytonaAdmissionPermit]:
    admission = DaytonaAdmission(max_active_leases=1)
    permit = await admission.acquire(deadline=asyncio.get_running_loop().time() + 5)
    return admission, permit


def _cleanup_coroutine(platform: _ScriptedPlatform, permit: DaytonaAdmissionPermit, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "platform": platform,
        "sandbox": _sandbox(),
        "sandbox_id": "sb-ephemeral",
        "mount_path": "/mnt/data",
        "permit": permit,
        "confirm_poll_interval_s": 0.01,
        "confirm_timeout_s": 0.25,
    }
    kwargs.update(overrides)
    return cleanup_child_runtime_async(**kwargs)


def _sandbox() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(id="sb-ephemeral", fs=_FsStub())


@pytest.mark.asyncio
async def test_permit_released_only_after_confirmed_absent() -> None:
    """Success: request -> destroying -> not-found; release strictly after absence."""
    platform = _ScriptedPlatform(states=["destroying", "started", None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.deletes == ["sb-ephemeral"]
    assert platform.probes == 3
    assert permit._released is True


@pytest.mark.asyncio
async def test_request_acceptance_alone_never_releases() -> None:
    """Merely accepted deletion without absence keeps the coroutine waiting."""
    platform = _ScriptedPlatform(states=["destroying", "destroying", "destroying", None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.probes == 4  # held through every transitional observation
    assert permit._released is True


@pytest.mark.asyncio
async def test_unconfirmed_teardown_is_explicit_quarantine_failure() -> None:
    """Slow deletion: bounded wait exhausts -> typed failure AND no leaked permit."""
    platform = _ScriptedPlatform(states=["destroying"] * 100)
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError) as excinfo:
        await _cleanup_coroutine(platform, permit)
    assert "absence unconfirmed" in str(excinfo.value)
    assert permit._released is True  # quarantine releases once, never silently


@pytest.mark.asyncio
async def test_delete_request_error_still_probes_and_surfaces_error() -> None:
    """Provider error on the request: confirmation still runs; first error surfaces."""
    platform = _ScriptedPlatform(states=[None], delete_error=RuntimeError("provider 503"))
    _, permit = await _take_permit()
    with pytest.raises(RuntimeError, match="provider 503"):
        await _cleanup_coroutine(platform, permit)
    assert platform.probes == 1  # absence still probed after a failed request
    assert permit._released is True


@pytest.mark.asyncio
async def test_provider_error_state_is_quarantine_failure() -> None:
    platform = _ScriptedPlatform(states=["error"])
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError):
        await _cleanup_coroutine(platform, permit)
    assert permit._released is True


@pytest.mark.asyncio
async def test_already_absent_sandbox_releases_promptly() -> None:
    platform = _ScriptedPlatform(states=[None])
    _, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    assert platform.probes == 1
    assert permit._released is True


@pytest.mark.asyncio
async def test_double_release_is_idempotent() -> None:
    platform = _ScriptedPlatform(states=[None])
    admission, permit = await _take_permit()
    await _cleanup_coroutine(platform, permit)
    permit.release()
    permit.release()
    # A real second acquisition must fit in the bounded semaphore (no over-release).
    permit2 = await admission.acquire(deadline=asyncio.get_running_loop().time() + 5)
    permit2.release()


@pytest.mark.asyncio
async def test_confirmation_timeout_never_leaks_permit() -> None:
    """Timeout path: permit always ends released exactly once (quarantine semantics)."""
    platform = _ScriptedPlatform(states=["destroying"] * 1000)
    _, permit = await _take_permit()
    with pytest.raises(ChildRuntimeCleanupError):
        await _cleanup_coroutine(platform, permit, confirm_timeout_s=0.05)
    assert permit._released is True
