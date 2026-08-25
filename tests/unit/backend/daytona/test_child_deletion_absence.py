"""QRE-151: ephemeral child admission stays owned until confirmed Sandbox absence.

Timeouts in these tests are real-wall-clock with tiny budgets; every probe
script is deterministic in call count, not in timing windows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from fleet_rlm.daytona.recursive_child_runtime import cleanup_child_runtime_async
from fleet_rlm.daytona.session_manager import DaytonaAdmission, DaytonaAdmissionPermit
from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError


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
