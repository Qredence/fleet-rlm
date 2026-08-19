"""Confirmed Sandbox deletion lifecycle: requested, deleting, absent.

Fleet ownership must never treat a fire-and-forget provider deletion request
as cleanup completion. Daytona deletion is asynchronous: the API accepts a
request, the Sandbox passes through observable states, and only a confirmed
terminal observation (provider not-found or ``destroyed``) closes ownership.

This module is the single host authority for that characterization
(QRE-150/QRE-151):

- :func:`classify_deletion_phase` maps raw provider state to the three public
  phases ``requested`` / ``deleting`` / ``absent`` plus terminal ``failed``
  for provider error states.
- :func:`confirm_absence` polls a caller-owned probe until confirmed absence,
  a state/protocol probe error, or a bounded timeout, returning a closed
  outcome value instead of raising so cleanup paths classify failure without
  swallowing it.

Provider authority stays in ``SandboxPlatform`` adapters; this module performs
no SDK I/O itself and never embeds credentials.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias

from fleet_rlm.daytona.errors import sanitize_failure_text

__all__ = [
    "DEFAULT_CONFIRM_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "AbsenceConfirmation",
    "AbsenceOutcome",
    "AbsenceProbeError",
    "AbsenceTimeout",
    "DeletionPhase",
    "DeletionStateProbe",
    "classify_deletion_phase",
    "confirm_absence",
]

DeletionPhase = Literal["requested", "deleting", "absent", "failed"]
"""Observable deletion lifecycle for one provider Sandbox.

- ``requested``: the deletion request was accepted but the Sandbox still
  reports an owning state (running/stopped/archived/creating/unknown).
- ``deleting``: the provider reports a transitional teardown state.
- ``absent``: the provider reports a terminal destroyed state or explicit
  not-found (404). Only ``absent`` closes ownership.
- ``failed``: the provider reports an error state; teardown cannot be
  observed to complete.
"""

#: Raw provider states that close ownership immediately.
_ABSENT_STATES = frozenset({"destroyed", "deleted"})
#: Raw provider states that mean teardown is in flight.
_DELETING_STATES = frozenset({"destroying", "deleting", "archiving", "stopping"})
#: Raw provider states that mean teardown failed on the provider side.
_FAILED_STATES = frozenset({"error", "build_failed"})

#: Default budget for one inline absence-confirmation wait.
DEFAULT_CONFIRM_TIMEOUT_S = 60.0
#: Default interval between two absence probes.
DEFAULT_POLL_INTERVAL_S = 1.0


class DeletionStateProbe(Protocol):
    """Caller-owned one-Sandbox lookup; returns ``None`` on explicit not-found."""

    def __call__(self, sandbox_id: str) -> Awaitable[Any | None]: ...


def _raw_state(target: Any) -> str:
    raw = getattr(target, "state", None)
    if raw is None:
        raw = getattr(target, "status", None)
    return str(getattr(raw, "value", raw) or "").strip().lower()


def classify_deletion_phase(raw_state: Any) -> DeletionPhase:
    """Map one raw provider state string (or enum) onto the public phase model."""
    text = str(getattr(raw_state, "value", raw_state) or "").strip().lower()
    if text in _ABSENT_STATES:
        return "absent"
    if text in _DELETING_STATES:
        return "deleting"
    if text in _FAILED_STATES:
        return "failed"
    return "requested"


@dataclass(frozen=True, slots=True)
class AbsenceConfirmation:
    """Confirmed: the provider reports the Sandbox absent (not-found/destroyed)."""

    sandbox_id: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[True] = True


@dataclass(frozen=True, slots=True)
class AbsenceTimeout:
    """The confirmation budget elapsed without an absent observation."""

    sandbox_id: str
    last_state: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


@dataclass(frozen=True, slots=True)
class AbsenceProbeError:
    """A probe call raised, or the provider surfaced a terminal error state."""

    sandbox_id: str
    error: str
    observations: tuple[str, ...]
    duration_s: float
    absent: Literal[False] = False


AbsenceOutcome: TypeAlias = AbsenceConfirmation | AbsenceTimeout | AbsenceProbeError
"""Closed outcome of one absence-confirmation wait; ``absent`` is authoritative."""


async def confirm_absence(
    *,
    probe: DeletionStateProbe,
    sandbox_id: str,
    timeout_s: float = DEFAULT_CONFIRM_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> AbsenceOutcome:
    """Poll ``probe`` until the Sandbox is confirmed absent or the budget closes.

    The probe contract matches ``SandboxPlatform.get``: ``None`` means explicit
    provider not-found; any object is interrogated for ``.state``/``.status``.
    The function never raises for slow deletion, probe errors, or provider
    error states; those are classified outcomes. ``asyncio.CancelledError``
    and other ``BaseException`` from the caller loop still propagate.

    Parameters:
        probe (DeletionStateProbe): Async lookup returning the Sandbox or ``None``.
        sandbox_id (str): The Sandbox identifier to confirm absent.
        timeout_s (float): Total confirmation budget in seconds.
        poll_interval_s (float): Delay between probes in seconds.
        clock (Callable[[], float]): Monotonic clock (test seam).
        sleep (Callable[[float], Awaitable[None]] | None): Async sleep (test seam);
            defaults to :func:`asyncio.sleep`.

    Returns:
        AbsenceOutcome: ``AbsenceConfirmation`` when the Sandbox is confirmed
        absent, ``AbsenceTimeout`` when the budget elapsed, or
        ``AbsenceProbeError`` for probe failures / provider error states.
    """
    if sleep is None:
        sleep = asyncio.sleep
    started = clock()
    observations: list[str] = []

    def note(state: str) -> None:
        if not observations or observations[-1] != state:
            observations.append(state)

    while True:
        try:
            target = await probe(sandbox_id)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            note("probe_error")
            return AbsenceProbeError(sandbox_id, sanitize_failure_text(exc), tuple(observations), clock() - started)
        if target is None:
            note("not_found")
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        state = _raw_state(target) or "unknown"
        note(state)
        phase = classify_deletion_phase(state)
        if phase == "absent":
            return AbsenceConfirmation(sandbox_id, tuple(observations), clock() - started)
        if phase == "failed":
            return AbsenceProbeError(
                sandbox_id,
                f"provider error state: {state}",
                tuple(observations),
                clock() - started,
            )
        if clock() - started >= timeout_s:
            return AbsenceTimeout(sandbox_id, state, tuple(observations), clock() - started)
        await sleep(poll_interval_s)
