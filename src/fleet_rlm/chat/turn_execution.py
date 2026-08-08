"""Private post-preparation Turn execution state machine."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeAlias
from uuid import UUID

from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
from fleet_rlm.chat.turn_lifecycle import (
    CommittedTurnReceipt,
    ExecuteTurn,
    FailedRunReceipt,
    TurnFailure,
    TurnFinalization,
    TurnLifecycle,
)
from fleet_rlm.chat.turn_preparation import PreparedTurn
from fleet_rlm.observability.turn_tracing import annotate_trace_io, turn_phase_span
from fleet_rlm.rlm.context import RLMExecutionContext
from fleet_rlm.rlm.dspy_contract import RLMUsage, empty_rlm_usage
from fleet_rlm.rlm.events import (
    TERMINAL_DETAIL_TYPES,
    EventRecorder,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunFailedMessage,
    RunStarted,
    RunTimedOut,
    RuntimeEvent,
)
from fleet_rlm.rlm.outcome import RLMOutcome


class TurnEventStream(AsyncIterator[RuntimeEvent], Protocol):
    @property
    def outcome(self) -> RLMOutcome | None: ...

    async def aclose(self) -> None: ...

    async def wait_owned(self) -> None: ...


class TurnRunner(Protocol):
    def stream(self, context: RLMExecutionContext) -> TurnEventStream: ...


@dataclass(slots=True)
class _ClaimHeartbeat:
    task: asyncio.Task[None]
    lost: asyncio.Event


async def _shield_cleanup(awaitable: Awaitable[object]) -> None:
    """Complete owned settlement/cleanup even if the caller is cancelled."""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise


async def _stop_heartbeat(heartbeat: _ClaimHeartbeat | None) -> None:
    if heartbeat is None:
        return
    heartbeat.task.cancel()
    await asyncio.gather(heartbeat.task, return_exceptions=True)


def _terminal(
    recorder: EventRecorder,
    receipt: CommittedTurnReceipt | FailedRunReceipt,
    *,
    trace_id: str | None = None,
) -> RuntimeEvent:
    if isinstance(receipt, CommittedTurnReceipt):
        return recorder.record(
            RunCompleted(
                checkpoint_version=receipt.checkpoint_version,
                delivery="live",
                trace_id=trace_id,
            )
        )
    if receipt.terminal_status == "cancelled":
        return recorder.record(RunCancelled())
    if receipt.terminal_status == "timeout":
        return recorder.record(RunTimedOut())
    if receipt.failure_code == "preparation_failed":
        return recorder.record(RunFailed(code="preparation_failed", message="Turn could not be prepared"))
    if receipt.failure_code == "commit_failed":
        return recorder.record(RunFailed(code="commit_failed", message="Turn could not be committed"))
    message = receipt.public_message.strip() if receipt.public_message else ""
    if message == "Turn output is too large":
        public_message: RunFailedMessage = "Turn output is too large"
    elif message == "Turn output is invalid":
        public_message = "Turn output is invalid"
    else:
        public_message = "Turn failed"
    return recorder.record(RunFailed(code="execution_failed", message=public_message))


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


class _ClaimLost:
    """Internal result used when the durable claim waiter wins a race."""


_FinalizationWait: TypeAlias = TurnFinalization | _ClaimLost | None


@dataclass(slots=True)
class _ExecutionState:
    recorder: EventRecorder
    heartbeat: _ClaimHeartbeat | None
    claim_loss_waiter: asyncio.Task[bool] | None
    pending_event: asyncio.Task[RuntimeEvent] | None = None
    stream: TurnEventStream | None = None
    finalization_task: asyncio.Task[TurnFinalization] | None = None
    settled: bool = False
    cleanup_handed_off: bool = False


class TurnExecutionDriver:
    """Own the complete post-preparation execution and settlement protocol."""

    def __init__(
        self,
        *,
        lifecycle: TurnLifecycle,
        runner: TurnRunner,
        projector: CommittedTurnEventProjector,
        cleanup: TurnCleanupSupervisor,
        claim_loss_fence: Callable[[UUID], Awaitable[None]] | None,
        turn_timeout_seconds: float,
        revoke_claim: Callable[[ExecuteTurn, RLMUsage], Awaitable[FailedRunReceipt | None]],
    ) -> None:
        self._lifecycle = lifecycle
        self._runner = runner
        self._projector = projector
        self._cleanup = cleanup
        self._claim_loss_fence = claim_loss_fence
        self._turn_timeout_seconds = turn_timeout_seconds
        self._revoke_claim = revoke_claim

    async def stream(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        heartbeat: _ClaimHeartbeat | None,
        *,
        trace_id: str | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Drain provider events, settle the Turn, and hand off owned cleanup."""
        state = _ExecutionState(
            recorder=EventRecorder(turn.run_id, turn.session_id),
            heartbeat=heartbeat,
            claim_loss_waiter=(asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None),
        )
        trace_request = self._trace_request(prepared)
        try:
            state.stream = self._runner.stream(prepared.execution)
            async for event in self._drain_events(turn, prepared, state, trace_request, trace_id):
                yield event
            if state.settled:
                return

            outcome = state.stream.outcome or RLMOutcome(
                terminal_status="failed",
                public_error_message="Turn failed",
            )
            self._annotate_outcome(trace_request, outcome)
            receipt = await self._settle_outcome(turn, prepared, state, outcome, trace_request)
            if isinstance(receipt, _ClaimLost):
                yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                return

            self._annotate_receipt(trace_request, outcome, receipt)
            state.settled = True
            if isinstance(receipt, CommittedTurnReceipt):
                for event in self._projector.project(receipt.committed_turn, state.recorder, mode="live_suffix"):
                    yield event
            yield _terminal(state.recorder, receipt, trace_id=trace_id)
        except (GeneratorExit, asyncio.CancelledError):
            await self._settle_cancellation(turn, prepared, state)
            raise
        except Exception:
            if not state.settled:
                receipt = await self._recover_failure(turn, prepared, trace_request)
                if receipt is not None:
                    state.settled = True
                    yield _terminal(state.recorder, receipt, trace_id=trace_id)
                else:
                    yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
        finally:
            await self._close_execution(prepared, state)

    async def _drain_events(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        state: _ExecutionState,
        trace_request: str,
        trace_id: str | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        stream = state.stream
        assert stream is not None
        while True:
            next_event = asyncio.ensure_future(anext(stream))
            state.pending_event = next_event
            try:
                result = await self._wait_for_event(next_event, state.claim_loss_waiter)
            finally:
                if next_event.done():
                    state.pending_event = None
            if isinstance(result, _ClaimLost):
                await self._handoff_cleanup(
                    turn,
                    prepared,
                    state,
                    claim_lost=True,
                    claim_loss_usage=empty_rlm_usage(),
                )
                state.settled = True
                annotate_trace_io(request=trace_request, response_text="Turn failed", failed=True)
                yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                return
            if result is None:
                return
            if isinstance(result.detail, TERMINAL_DETAIL_TYPES):
                raise RuntimeError("runner emitted a terminal Runtime Event")
            state.recorder = EventRecorder(turn.run_id, turn.session_id, start_sequence=result.sequence)
            yield _with_trace_id(result, trace_id)

    async def _wait_for_event(
        self,
        next_event: asyncio.Task[RuntimeEvent],
        claim_loss_waiter: asyncio.Task[bool] | None,
    ) -> RuntimeEvent | _ClaimLost | None:
        waiters: set[asyncio.Future[Any]] = {next_event}
        if claim_loss_waiter is not None:
            waiters.add(claim_loss_waiter)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if claim_loss_waiter is not None and claim_loss_waiter in done:
            next_event.cancel()
            await asyncio.gather(next_event, return_exceptions=True)
            return _ClaimLost()
        try:
            return next_event.result()
        except StopAsyncIteration:
            return None

    async def _settle_outcome(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        state: _ExecutionState,
        outcome: RLMOutcome,
        trace_request: str,
    ) -> TurnFinalization | _ClaimLost:
        if outcome.terminal_status in {"timeout", "cancelled"}:
            status = "timeout" if outcome.terminal_status == "timeout" else "cancelled"
            receipt = await self._lifecycle.settle(
                turn,
                TurnFailure(
                    status,
                    status,
                    outcome.public_error_message or ("Turn timed out" if status == "timeout" else "Turn cancelled"),
                    outcome.usage,
                ),
            )
            await self._handoff_cleanup(turn, prepared, state)
            await asyncio.sleep(0)
            return receipt

        state.finalization_task = asyncio.create_task(
            self._finish_with_trace(turn, outcome, prepared),
            name="fleet-turn-finalization",
        )
        execution_deadline = float(
            getattr(
                prepared.execution,
                "deadline",
                asyncio.get_running_loop().time() + self._turn_timeout_seconds,
            )
        )
        remaining = max(0.0, execution_deadline - asyncio.get_running_loop().time())
        result = await self._wait_for_finalization(state.finalization_task, state.claim_loss_waiter, remaining)
        if isinstance(result, _ClaimLost):
            await self._handoff_cleanup(
                turn,
                prepared,
                state,
                claim_lost=True,
                claim_loss_usage=outcome.usage,
                finalization_task=state.finalization_task,
            )
            state.settled = True
            annotate_trace_io(request=trace_request, response_text="Turn failed", failed=True)
            return result
        if result is None:
            await self._stop_claim_waiter(state)
            receipt = await self._lifecycle.settle(
                turn,
                TurnFailure("timeout", "timeout", "Turn timed out", outcome.usage),
            )
            await self._handoff_cleanup(
                turn,
                prepared,
                state,
                finalization_task=state.finalization_task,
            )
            await asyncio.sleep(0)
            return receipt
        await self._stop_claim_waiter(state)
        return result

    async def _wait_for_finalization(
        self,
        finalization_task: asyncio.Task[TurnFinalization],
        claim_loss_waiter: asyncio.Task[bool] | None,
        remaining: float,
    ) -> _FinalizationWait:
        waiters: set[asyncio.Future[Any]] = {finalization_task}
        if claim_loss_waiter is not None:
            waiters.add(claim_loss_waiter)
        done, _ = await asyncio.wait(
            waiters,
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A completed finalization wins a simultaneous claim-loss race.
        if finalization_task in done:
            return finalization_task.result()
        if claim_loss_waiter is not None and claim_loss_waiter in done:
            return _ClaimLost()
        return None

    async def _finish_with_trace(
        self,
        turn: ExecuteTurn,
        resolution: RLMOutcome | TurnFailure,
        prepared: PreparedTurn,
    ) -> TurnFinalization:
        terminal_status = resolution.terminal_status
        settlement_inputs: dict[str, object] = {
            "terminal_status": terminal_status,
            "has_prediction": isinstance(resolution, RLMOutcome) and resolution.prediction is not None,
        }
        if isinstance(resolution, RLMOutcome):
            settlement_inputs["duration_ms"] = resolution.duration_ms
            settlement_inputs["artifact_candidate_count"] = len(resolution.artifact_candidates)
            settlement_inputs["iterations"] = resolution.usage.get("iterations")
        with turn_phase_span("Turn.settlement", inputs=settlement_inputs):
            return await self._lifecycle.finish(
                turn,
                resolution,
                artifact_sink=prepared.artifact_sink,
                result_snapshot_sink=prepared.result_snapshot_sink,
            )

    async def _settle_cancellation(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        state: _ExecutionState,
    ) -> None:
        if state.settled:
            return
        try:
            await self._cancel_pending_event(state)
            await asyncio.shield(
                self._lifecycle.settle(
                    turn,
                    TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                )
            )
            await self._handoff_cleanup(turn, prepared, state)
            await asyncio.sleep(0)
        except Exception:
            pass

    async def _recover_failure(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        trace_request: str,
    ) -> FailedRunReceipt | CommittedTurnReceipt | None:
        annotate_trace_io(request=trace_request, response_text="Turn failed", failed=True)
        try:
            return await asyncio.shield(
                self._finish_with_trace(
                    turn,
                    TurnFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
                    prepared,
                )
            )
        except Exception:
            return None

    async def _handoff_cleanup(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        state: _ExecutionState,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
        finalization_task: asyncio.Task[TurnFinalization] | None = None,
    ) -> None:
        self._submit_cleanup(
            turn,
            state.stream,
            prepared,
            state.heartbeat,
            finalization_task,
            claim_lost=claim_lost,
            claim_loss_usage=claim_loss_usage,
        )
        state.cleanup_handed_off = True
        await self._stop_claim_waiter(state)
        state.heartbeat = None

    async def _close_execution(self, prepared: PreparedTurn, state: _ExecutionState) -> None:
        with turn_phase_span("Turn.cleanup", inputs={"cleanup_owned": not state.cleanup_handed_off}):
            await self._cancel_pending_event(state)
            await self._stop_claim_waiter(state)
            await _stop_heartbeat(state.heartbeat)
            if not state.cleanup_handed_off:
                await _shield_cleanup(prepared.aclose())

    @staticmethod
    async def _cancel_pending_event(state: _ExecutionState) -> None:
        pending = state.pending_event
        if pending is None or pending.done():
            state.pending_event = None
            return
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        state.pending_event = None

    async def _stop_claim_waiter(self, state: _ExecutionState) -> None:
        waiter = state.claim_loss_waiter
        if waiter is None:
            return
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        state.claim_loss_waiter = None

    def _submit_cleanup(
        self,
        turn: ExecuteTurn,
        stream: TurnEventStream | None,
        prepared: PreparedTurn,
        heartbeat: _ClaimHeartbeat | None,
        finalization_task: asyncio.Task[TurnFinalization] | None = None,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
    ) -> None:
        async def cleanup() -> None:
            try:
                committed = False
                if claim_lost:
                    receipt = await self._revoke_claim(turn, claim_loss_usage or empty_rlm_usage())
                    # A racing commit wins: no fence and no settlement release
                    # against a committed Run, but owned resources still close.
                    committed = receipt is None
                    if not committed and self._claim_loss_fence is not None:
                        await self._claim_loss_fence(turn.session_id)
                if stream is not None:
                    with contextlib.suppress(BaseException):
                        await stream.aclose()
                    await stream.wait_owned()
                if finalization_task is not None:
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(finalization_task)
                await prepared.aclose()
                if not committed:
                    await self._lifecycle.complete_settling(turn)
            finally:
                await _stop_heartbeat(heartbeat)

        self._cleanup.submit(cleanup())

    @staticmethod
    def _trace_request(prepared: PreparedTurn) -> str:
        request = getattr(prepared.execution, "request", "")
        return request if isinstance(request, str) else ""

    @staticmethod
    def _annotate_outcome(trace_request: str, outcome: RLMOutcome) -> None:
        annotate_trace_io(
            request=trace_request,
            response_text=(outcome.prediction.display_text if outcome.prediction else outcome.public_error_message),
            response_outputs=(dict(outcome.prediction.outputs) if outcome.prediction else None),
            failed=not outcome.succeeded,
        )

    @staticmethod
    def _annotate_receipt(
        trace_request: str,
        outcome: RLMOutcome,
        receipt: TurnFinalization,
    ) -> None:
        if isinstance(receipt, FailedRunReceipt) and outcome.succeeded:
            annotate_trace_io(
                request=trace_request,
                response_text=receipt.public_message or "Turn failed",
                failed=True,
            )


__all__ = [
    "TurnEventStream",
    "TurnExecutionDriver",
    "TurnRunner",
    "_ClaimHeartbeat",
    "_shield_cleanup",
    "_stop_heartbeat",
    "_terminal",
    "_with_trace_id",
]
