"""QRE-150: deterministic deletion lifecycle characterization.

The fake provider demonstrates request-accepted, deleting, and absent as
distinct observable states so host code never treats request acceptance as
cleanup completion.
"""

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
