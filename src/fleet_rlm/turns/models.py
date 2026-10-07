"""Models and internal states for Turn execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, Self, TypeAlias, TypeVar
from uuid import UUID

from fleet_rlm.observability.tracing import (
    record_settlement_status,
)
from fleet_rlm.observability.turn_capture import EventCapture
from fleet_rlm.rlm.events import (
    EventRecorder,
    RunCompleted,
    RunStarted,
    RuntimeEvent,
)
from fleet_rlm.rlm.execution import RLMExecutionContext
from fleet_rlm.rlm.ownership import OwnedEffect
from fleet_rlm.rlm.result import RLMOutcome
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from fleet_rlm.sessions.run_state import (
    ClaimedRun,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunSettlement,
)
from fleet_rlm.turns.preparation import PreparedTurn

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _mark_capture_stop(capture: EventCapture | None, reason: str) -> None:
    """Record a non-terminal stop reason on the active Turn capture, if any."""
    if capture is None:
        return
    try:
        capture.mark_stop_reason(reason)
    except Exception:
        logger.warning("Turn capture stop reason failed", exc_info=True)


@dataclass(frozen=True, slots=True)
class OpenTurnCommand:
    """Validated Turn intent after local-scope and schema validation."""

    access: TurnAccess
    session_id: UUID
    input: TurnInput
    idempotency_key: str
    proposed_run_id: UUID

    def __post_init__(self) -> None:
        key = self.idempotency_key
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 128
            or key != key.strip()
            or not key.isprintable()
            or any(char.isspace() for char in key)
        ):
            raise ValueError("idempotency_key must contain 1..128 printable non-whitespace characters")


@dataclass(slots=True)
class ClaimHeartbeat:
    task: asyncio.Task[None]
    lost: asyncio.Event
    definitive_loss: bool = False


async def shield_cleanup(awaitable: Awaitable[T]) -> T:
    """Complete an awaitable despite caller cancellation."""
    effect = OwnedEffect.start(awaitable)
    settled = await effect.settle()
    if settled.caller_cancelled:
        raise asyncio.CancelledError
    return settled.result()


async def stop_heartbeat(heartbeat: ClaimHeartbeat | None) -> None:
    if heartbeat is None:
        return
    heartbeat.task.cancel()
    await asyncio.gather(heartbeat.task, return_exceptions=True)


class RunEventStream(Protocol):
    """Async observation stream owned by the TurnRuntime."""

    def __aiter__(self) -> Self: ...

    async def __anext__(self) -> RuntimeEvent: ...

    @property
    def outcome(self) -> RLMOutcome | None: ...

    async def aclose(self) -> None: ...

    async def wait_owned(self) -> None: ...


class RunRunner(Protocol):
    def stream(self, context: RLMExecutionContext) -> RunEventStream: ...


def _attach_preparation_trace_id(
    prepared: PreparedTurn,
    trace_id: str | None,
    span_id: str | None = None,
) -> PreparedTurn:
    """Attach a preparation trace identifier for internal phase correlation."""
    if not trace_id:
        return prepared
    try:
        return replace(
            prepared,
            preparation_trace_id=trace_id,
            preparation_span_id=span_id,
        )  # type: ignore[type-var]
    except (TypeError, AttributeError, ValueError):
        return prepared


_PREPARATION_CLEANUP_TIMEOUT_S = 1.0


@dataclass(slots=True)
class _PreparationState:
    run: ClaimedRun
    heartbeat: ClaimHeartbeat | None
    preparation_task: asyncio.Task[PreparedTurn] | None = None
    heartbeat_lost: asyncio.Task[bool] | None = None
    quarantine: set[asyncio.Task[Any]] | None = None
    cleanup_error: BaseException | None = None

    def __post_init__(self) -> None:
        if self.quarantine is None:
            self.quarantine = set()


async def _wait_stream_owned(stream: RunEventStream) -> None:
    wait_owned = getattr(stream, "wait_owned", None)
    if callable(wait_owned):
        await wait_owned()


async def _close_stream_owned(
    stream: RunEventStream | None,
    remember: Callable[[BaseException], None],
    *,
    wait_timeout: float | None = None,
    retain_waiter: Callable[[asyncio.Task[None]], None] | None = None,
) -> None:
    """Close and wait for one provider stream while retaining the first failure."""
    if stream is None:
        return
    try:
        await stream.aclose()
    except BaseException as exc:
        remember(exc)
    if wait_timeout is None:
        try:
            await _wait_stream_owned(stream)
        except BaseException as exc:
            remember(exc)
        return

    waiter = asyncio.create_task(_wait_stream_owned(stream), name="fleet-stream-owned-wait")
    done, _ = await asyncio.wait((waiter,), timeout=max(0.0, wait_timeout))
    if not done:
        if retain_waiter is not None:
            retain_waiter(waiter)
        remember(TimeoutError("owned execution did not drain before the cleanup deadline"))
        return
    try:
        waiter.result()
    except BaseException as exc:
        remember(exc)


def _defer_stream_runtime(stream: RunEventStream | None) -> None:
    """Tell a resident Runner to hold its Session lane through cleanup."""
    if stream is None:
        return
    defer = getattr(stream, "defer_runtime_release", None)
    if callable(defer):
        defer()


def _mark_stream_runtime(stream: RunEventStream | None, *, committed: bool) -> None:
    """Record the durable outcome on a resident runtime token when supported."""
    if stream is None:
        return
    method = getattr(stream, "mark_committed" if committed else "mark_tainted", None)
    if callable(method):
        method()


async def _release_stream_runtime(
    stream: RunEventStream | None,
    remember: Callable[[BaseException], None],
) -> None:
    """Release a resident Session lane after all prepared resources settle."""
    if stream is None:
        return
    release = getattr(stream, "release_runtime", None)
    if callable(release):
        try:
            await release()
        except BaseException as exc:
            remember(exc)


@dataclass(slots=True)
class _ExecutionState:
    recorder: EventRecorder
    heartbeat: ClaimHeartbeat | None
    claim_loss_waiter: asyncio.Task[bool] | None
    pending_event: asyncio.Task[RuntimeEvent] | None = None
    stream: RunEventStream | None = None
    finalization_task: asyncio.Task[RunSettlement] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    settled: bool = False
    cleanup_handed_off: bool = False


class _ClaimLost:
    """Internal marker returned when the claim-loss waiter wins a race."""


_FinalizationWait: TypeAlias = RunSettlement | _ClaimLost | None


def _record_settlement(receipt: RunSettlement) -> RunSettlement:
    """Annotate the active trace with the durable Fleet settlement result."""
    if isinstance(receipt, CommittedTurnReceipt):
        record_settlement_status("completed", durable=True)
    elif isinstance(receipt, FailedRunReceipt):
        record_settlement_status(receipt.terminal_status, durable=receipt.durable)
    return receipt


def _heartbeat_claim_lost(state: _ExecutionState) -> bool:
    return state.heartbeat is not None and state.heartbeat.lost.is_set()


def _with_trace_id(event: RuntimeEvent, trace_id: str | None) -> RuntimeEvent:
    if not trace_id:
        return event
    detail = event.detail
    if isinstance(detail, RunStarted) and detail.trace_id is None:
        return replace(event, detail=RunStarted(delivery=detail.delivery, trace_id=trace_id))
    if isinstance(detail, RunCompleted) and detail.trace_id is None:
        return replace(
            event,
            detail=RunCompleted(
                checkpoint_version=detail.checkpoint_version,
                delivery=detail.delivery,
                duration_ms=detail.duration_ms,
                trace_id=trace_id,
            ),
        )
    return event


__all__ = [
    "_PREPARATION_CLEANUP_TIMEOUT_S",
    "ClaimHeartbeat",
    "OpenTurnCommand",
    "RunEventStream",
    "RunRunner",
    "_ClaimLost",
    "_ExecutionState",
    "_FinalizationWait",
    "_PreparationState",
    "_attach_preparation_trace_id",
    "_close_stream_owned",
    "_defer_stream_runtime",
    "_heartbeat_claim_lost",
    "_mark_capture_stop",
    "_mark_stream_runtime",
    "_record_settlement",
    "_release_stream_runtime",
    "_wait_stream_owned",
    "_with_trace_id",
    "shield_cleanup",
    "stop_heartbeat",
]
