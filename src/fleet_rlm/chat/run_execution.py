"""Private post-preparation Run execution state machine."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, Self, TypeAlias
from uuid import UUID

from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunFailure,
    RunLifecycle,
    RunSettlement,
)
from fleet_rlm.chat.run_ownership import ClaimHeartbeat, shield_cleanup, stop_heartbeat
from fleet_rlm.chat.run_preparation import PreparedRun
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


class RunEventStream(Protocol):
    """Async observation stream with settlement accessors (3.11-safe Protocol).

    Do not inherit from ``AsyncIterator``: on Python 3.11 a Protocol may only
    subclass other Protocols, and ``collections.abc.AsyncIterator`` is not one.
    """

    def __aiter__(self) -> Self: ...

    async def __anext__(self) -> RuntimeEvent: ...

    @property
    def outcome(self) -> RLMOutcome | None: ...

    async def aclose(self) -> None: ...

    async def wait_owned(self) -> None: ...


class RunRunner(Protocol):
    def stream(self, context: RLMExecutionContext) -> RunEventStream: ...


def terminal(
    recorder: EventRecorder,
    receipt: CommittedTurnReceipt | FailedRunReceipt,
    *,
    trace_id: str | None = None,
) -> RuntimeEvent:
    """Project a durable receipt into the live terminal RuntimeEvent."""
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


async def _wait_stream_owned(stream: RunEventStream) -> None:
    wait_owned = getattr(stream, "wait_owned", None)
    if callable(wait_owned):
        await wait_owned()


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


class _ClaimLost:
    """Internal result used when the durable claim waiter wins a race."""


_FinalizationWait: TypeAlias = RunSettlement | _ClaimLost | None


@dataclass(slots=True)
class _ExecutionState:
    recorder: EventRecorder
    heartbeat: ClaimHeartbeat | None
    claim_loss_waiter: asyncio.Task[bool] | None
    pending_event: asyncio.Task[RuntimeEvent] | None = None
    stream: RunEventStream | None = None
    finalization_task: asyncio.Task[RunSettlement] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    on_cleanup: Callable[[asyncio.Task[None]], None] | None = None
    settled: bool = False
    cleanup_handed_off: bool = False


class RunExecutionDriver:
    """Own the complete post-preparation execution and settlement protocol."""

    def __init__(
        self,
        *,
        lifecycle: RunLifecycle,
        runner: RunRunner,
        projector: CommittedTurnEventProjector,
        cleanup: RunCleanupSupervisor,
        claim_loss_fence: Callable[[UUID], Awaitable[None]] | None,
        turn_timeout_seconds: float,
        revoke_claim: Callable[[ClaimedRun, RLMUsage], Awaitable[FailedRunReceipt | None]],
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
        run: ClaimedRun,
        prepared: PreparedRun,
        heartbeat: ClaimHeartbeat | None,
        *,
        trace_id: str | None,
        on_settlement: Callable[[RunSettlement], None] | None = None,
        on_cleanup: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Drain provider events, settle the Run, and hand off owned cleanup."""
        state = _ExecutionState(
            recorder=EventRecorder(run.run_id, run.session_id),
            heartbeat=heartbeat,
            claim_loss_waiter=(asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None),
            on_cleanup=on_cleanup,
        )
        trace_request = self._trace_request(prepared)
        try:
            state.stream = self._runner.stream(prepared.execution)
            async for event in self._drain_events(run, prepared, state, trace_request, trace_id):
                yield event
            if state.settled:
                return

            outcome = state.stream.outcome or RLMOutcome(
                terminal_status="failed",
                public_error_message="Turn failed",
            )
            self._annotate_outcome(trace_request, outcome)
            receipt = await self._settle_outcome(run, prepared, state, outcome, trace_request)
            if isinstance(receipt, _ClaimLost):
                yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                return

            self._annotate_receipt(trace_request, outcome, receipt)
            if on_settlement is not None:
                on_settlement(receipt)
            state.settled = True
            if isinstance(receipt, CommittedTurnReceipt):
                for event in self._projector.project(receipt.committed_turn, state.recorder, mode="live_suffix"):
                    yield event
            yield terminal(state.recorder, receipt, trace_id=trace_id)
        except (GeneratorExit, asyncio.CancelledError):
            await self._settle_cancellation(run, prepared, state, on_settlement=on_settlement)
            raise
        except Exception:
            if not state.settled:
                receipt = await self._recover_failure(run, prepared, trace_request)
                if receipt is not None:
                    if on_settlement is not None:
                        on_settlement(receipt)
                    state.settled = True
                    yield terminal(state.recorder, receipt, trace_id=trace_id)
                else:
                    yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
        finally:
            await self._close_execution(prepared, state)

    async def _drain_events(
        self,
        run: ClaimedRun,
        prepared: PreparedRun,
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
                await self._handoff_cleanup_or_drain(
                    run,
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
            state.recorder = EventRecorder(run.run_id, run.session_id, start_sequence=result.sequence)
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
        run: ClaimedRun,
        prepared: PreparedRun,
        state: _ExecutionState,
        outcome: RLMOutcome,
        trace_request: str,
    ) -> RunSettlement | _ClaimLost:
        if outcome.terminal_status in {"timeout", "cancelled"}:
            status = "timeout" if outcome.terminal_status == "timeout" else "cancelled"
            failure = RunFailure(
                status,
                status,
                outcome.public_error_message or ("Turn timed out" if status == "timeout" else "Turn cancelled"),
                outcome.usage,
            )
            # Revoke the in-memory fence before durable settlement so detached
            # recursive workers cannot acquire new child runtimes while cleanup
            # is still waiting for already-running work to finish.
            run.authority.revoke()
            receipt: FailedRunReceipt | None = None
            claim_lost = _heartbeat_claim_lost(state)
            if not claim_lost:
                try:
                    receipt = await self._lifecycle.settle(run, failure)
                except BaseException:
                    if not _heartbeat_claim_lost(state):
                        raise
                    claim_lost = True
                else:
                    claim_lost = _heartbeat_claim_lost(state)
            if claim_lost:
                await self._handoff_cleanup_or_drain(
                    run,
                    prepared,
                    state,
                    claim_lost=True,
                    claim_loss_usage=outcome.usage,
                    finalization_task=state.finalization_task,
                )
                state.settled = True
                return _ClaimLost()
            assert receipt is not None
            await self._handoff_cleanup_or_drain(
                run,
                prepared,
                state,
                finalization_task=state.finalization_task,
            )
            await asyncio.sleep(0)
            return receipt

        state.finalization_task = asyncio.create_task(
            self._finish_with_trace(run, outcome, prepared),
            name="fleet-turn-finalization",
        )
        # RLMExecutionContext.execution (ExecutionRuntime) owns the shared
        # Turn deadline established by the coordinator; the fallback only
        # protects legacy PreparedRun doubles without the deep context.
        execution_deadline = self._execution_deadline(prepared)
        remaining = max(0.0, execution_deadline - asyncio.get_running_loop().time())
        result = await self._wait_for_finalization(
            state.finalization_task,
            state.claim_loss_waiter,
            remaining,
            is_authority_revoked=lambda: run.authority.revoked,
        )
        if isinstance(result, _ClaimLost):
            await self._handoff_cleanup_or_drain(
                run,
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
            run.authority.revoke()
            try:
                receipt = await self._lifecycle.settle(
                    run,
                    RunFailure("timeout", "timeout", "Turn timed out", outcome.usage),
                )
            finally:
                # A persistence timeout must not bypass ownership of the
                # still-running finalization task or recursive workers.
                await self._handoff_cleanup_or_drain(
                    run,
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
        finalization_task: asyncio.Task[RunSettlement],
        claim_loss_waiter: asyncio.Task[bool] | None,
        remaining: float,
        *,
        is_authority_revoked: Callable[[], bool] | None = None,
    ) -> _FinalizationWait:
        waiters: set[asyncio.Future[Any]] = {finalization_task}
        if claim_loss_waiter is not None:
            waiters.add(claim_loss_waiter)
        done, _ = await asyncio.wait(
            waiters,
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A successful finalization wins a simultaneous claim-loss race. If
        # finalization failed after authority was revoked, the durable claim
        # loss must own cleanup instead of falling through generic recovery.
        if finalization_task in done:
            try:
                return finalization_task.result()
            except BaseException:
                claim_lost = bool(is_authority_revoked and is_authority_revoked())
                if claim_loss_waiter is not None and claim_loss_waiter.done() and not claim_loss_waiter.cancelled():
                    with contextlib.suppress(BaseException):
                        claim_lost = claim_lost or bool(claim_loss_waiter.result())
                if claim_lost:
                    return _ClaimLost()
                raise
        if claim_loss_waiter is not None and claim_loss_waiter in done:
            return _ClaimLost()
        return None

    async def _finish_with_trace(
        self,
        run: ClaimedRun,
        resolution: RLMOutcome | RunFailure,
        prepared: PreparedRun,
    ) -> RunSettlement:
        terminal_status = resolution.terminal_status
        settlement_inputs: dict[str, object] = {
            "terminal_status": terminal_status,
            "has_prediction": isinstance(resolution, RLMOutcome) and resolution.prediction is not None,
        }
        if isinstance(resolution, RLMOutcome):
            settlement_inputs["duration_ms"] = resolution.duration_ms
            settlement_inputs["artifact_candidate_count"] = len(resolution.artifact_candidates)
            settlement_inputs["iterations"] = resolution.usage.get("iterations")
            settlement_inputs["memory_candidate_count"] = len(resolution.memory_candidates)
        with turn_phase_span("Turn.settlement", inputs=settlement_inputs):
            finish_kwargs: dict[str, Any] = {}
            # PreparedRun owns this field; SimpleNamespace test doubles
            # predating P23 may omit it (no outbox intents then). The kwarg is
            # only forwarded when present so legacy lifecycle doubles keep
            # their narrower finish signatures.
            builder = getattr(prepared, "memory_intent_builder", None)
            if builder is not None:
                finish_kwargs["memory_intents_builder"] = builder
            return await self._lifecycle.finish(
                run,
                resolution,
                artifact_sink=prepared.artifact_sink,
                result_snapshot_sink=prepared.result_snapshot_sink,
                memory_promotion=prepared.post_commit_memory_promotion,
                **finish_kwargs,
            )

    async def _settle_cancellation(
        self,
        run: ClaimedRun,
        prepared: PreparedRun,
        state: _ExecutionState,
        *,
        on_settlement: Callable[[RunSettlement], None] | None = None,
    ) -> None:
        if state.settled:
            return

        async def settle_owned_cancellation() -> None:
            # Caller cancellation is an immediate loss of execution authority;
            # the durable settling transition and detached cleanup may lag.
            run.authority.revoke()
            with contextlib.suppress(BaseException):
                await self._cancel_pending_event(state)
            claim_lost = _heartbeat_claim_lost(state)
            cancellation_receipt: RunSettlement | None = None
            if not claim_lost:
                with contextlib.suppress(BaseException):
                    cancellation_receipt = await self._lifecycle.settle(
                        run,
                        RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                    )
                if cancellation_receipt is not None and on_settlement is not None:
                    on_settlement(cancellation_receipt)
                claim_lost = _heartbeat_claim_lost(state)
            # Cleanup handoff is independent of durable settlement success. The
            # owned stream must still drain recursive workers before Run
            # resources are released when persistence itself is unavailable.
            await self._handoff_cleanup_or_drain(
                run,
                prepared,
                state,
                claim_lost=claim_lost,
                claim_loss_usage=empty_rlm_usage() if claim_lost else None,
                finalization_task=state.finalization_task,
            )
            await asyncio.sleep(0)

        # A second cancellation must not interrupt the handoff between durable
        # settlement and ownership transfer; otherwise the outer finally could
        # close PreparedRun while a recursive worker still owns its resources.
        await shield_cleanup(settle_owned_cancellation())

    async def _recover_failure(
        self,
        run: ClaimedRun,
        prepared: PreparedRun,
        trace_request: str,
    ) -> FailedRunReceipt | CommittedTurnReceipt | None:
        annotate_trace_io(request=trace_request, response_text="Turn failed", failed=True)
        try:
            return await asyncio.shield(
                self._finish_with_trace(
                    run,
                    RunFailure("failed", "execution_failed", "Turn failed", empty_rlm_usage()),
                    prepared,
                )
            )
        except Exception:
            return None

    async def _handoff_cleanup(
        self,
        run: ClaimedRun,
        prepared: PreparedRun,
        state: _ExecutionState,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
        finalization_task: asyncio.Task[RunSettlement] | None = None,
    ) -> None:
        state.cleanup_task = self._submit_cleanup(
            run,
            state.stream,
            prepared,
            state.heartbeat,
            finalization_task,
            claim_lost=claim_lost,
            claim_loss_usage=claim_loss_usage,
        )
        state.cleanup_handed_off = True
        if state.on_cleanup is not None:
            state.on_cleanup(state.cleanup_task)
        await self._stop_claim_waiter(state)
        state.heartbeat = None

    async def _handoff_cleanup_or_drain(
        self,
        run: ClaimedRun,
        prepared: PreparedRun,
        state: _ExecutionState,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
        finalization_task: asyncio.Task[RunSettlement] | None = None,
    ) -> None:
        """Preserve ownership if the detached cleanup queue cannot accept a job."""
        try:
            await self._handoff_cleanup(
                run,
                prepared,
                state,
                claim_lost=claim_lost,
                claim_loss_usage=claim_loss_usage,
                finalization_task=finalization_task,
            )
            return
        except BaseException:
            # A successful submission owns the resources even if stopping the
            # claim waiter is interrupted afterward; never drain the same
            # stream from two cleanup paths.
            if state.cleanup_handed_off:
                return
            # Capacity is checked before a Turn opens, but a shutdown race can
            # still reject submission.  Never fall through to PreparedRun.aclose
            # while an owned worker remains active; drain the ownership inline.
            state.cleanup_handed_off = True

            async def inline_cleanup() -> None:
                cleanup_error = False
                claim_cleanup_settled = not claim_lost
                try:
                    with contextlib.suppress(BaseException):
                        await self._stop_claim_waiter(state)
                    with contextlib.suppress(BaseException):
                        await stop_heartbeat(state.heartbeat)
                    state.heartbeat = None
                    if claim_lost:
                        try:
                            receipt = await self._revoke_claim(run, claim_loss_usage or empty_rlm_usage())
                        except BaseException:
                            receipt = None
                            cleanup_error = True
                        claim_cleanup_settled = receipt is not None
                        if receipt is not None and self._claim_loss_fence is not None:
                            try:
                                await self._claim_loss_fence(run.session_id)
                            except BaseException:
                                cleanup_error = True
                                claim_cleanup_settled = False
                    stream = state.stream
                    if stream is not None:
                        try:
                            await stream.aclose()
                        except BaseException:
                            cleanup_error = True
                        try:
                            await _wait_stream_owned(stream)
                        except BaseException:
                            cleanup_error = True
                    if finalization_task is not None:
                        # The finalization task is owned until it settles; a
                        # post-revocation lifecycle error is expected here.
                        with contextlib.suppress(BaseException):
                            await shield_cleanup(finalization_task)
                    try:
                        await prepared.aclose()
                    except BaseException:
                        cleanup_error = True
                    if not cleanup_error and claim_cleanup_settled:
                        with contextlib.suppress(BaseException):
                            await self._lifecycle.complete_settling(run)
                finally:
                    with contextlib.suppress(BaseException):
                        await self._stop_claim_waiter(state)
                    with contextlib.suppress(BaseException):
                        await stop_heartbeat(state.heartbeat)
                    state.heartbeat = None

            await shield_cleanup(inline_cleanup())

    async def _close_execution(self, prepared: PreparedRun, state: _ExecutionState) -> None:
        with turn_phase_span("Turn.cleanup", inputs={"cleanup_owned": not state.cleanup_handed_off}):
            await self._cancel_pending_event(state)
            await self._stop_claim_waiter(state)
            await stop_heartbeat(state.heartbeat)
            if not state.cleanup_handed_off:
                cleanup_error: BaseException | None = None
                stream = state.stream
                if stream is not None:
                    try:
                        await stream.aclose()
                    except BaseException as exc:
                        cleanup_error = exc
                    try:
                        await _wait_stream_owned(stream)
                    except BaseException as exc:
                        cleanup_error = cleanup_error or exc
                try:
                    await shield_cleanup(prepared.aclose())
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
                if cleanup_error is not None:
                    raise cleanup_error

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
        run: ClaimedRun,
        stream: RunEventStream | None,
        prepared: PreparedRun,
        heartbeat: ClaimHeartbeat | None,
        finalization_task: asyncio.Task[RunSettlement] | None = None,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
    ) -> asyncio.Task[None]:
        async def cleanup() -> None:
            committed = False
            cleanup_error: BaseException | None = None
            effective_claim_lost = claim_lost
            claim_cleanup_attempted = False

            async def apply_claim_loss() -> None:
                nonlocal committed, claim_cleanup_attempted, effective_claim_lost
                if claim_cleanup_attempted:
                    return
                claim_cleanup_attempted = True
                effective_claim_lost = True
                try:
                    receipt = await self._revoke_claim(run, claim_loss_usage or empty_rlm_usage())
                except BaseException as exc:
                    remember(exc)
                    return
                # A racing commit wins: no fence and no settlement release
                # against a committed Run.
                committed = receipt is None
                if receipt is not None and self._claim_loss_fence is not None:
                    try:
                        await self._claim_loss_fence(run.session_id)
                    except BaseException as exc:
                        remember(exc)

            def remember(exc: BaseException) -> None:
                nonlocal cleanup_error
                if cleanup_error is None:
                    cleanup_error = exc

            try:
                # Stop the heartbeat before the final durable transition. This
                # closes the window in which claim loss can be observed after a
                # local settlement decision but before complete_settling.
                await stop_heartbeat(heartbeat)
                if heartbeat is not None:
                    effective_claim_lost = effective_claim_lost or heartbeat.lost.is_set()
                if effective_claim_lost:
                    await apply_claim_loss()
                if stream is not None:
                    try:
                        await stream.aclose()
                    except BaseException as exc:
                        remember(exc)
                    try:
                        await _wait_stream_owned(stream)
                    except BaseException as exc:
                        remember(exc)
                if finalization_task is not None:
                    # A timeout/cancellation revokes authority before this
                    # task settles; its expected RunLifecycleUnavailableError
                    # is not a child-ownership failure. Waiting for the task
                    # is the ownership proof, regardless of its result.
                    with contextlib.suppress(BaseException):
                        await shield_cleanup(finalization_task)
                try:
                    await prepared.aclose()
                except BaseException as exc:
                    remember(exc)
                if heartbeat is not None and heartbeat.lost.is_set() and not effective_claim_lost:
                    await apply_claim_loss()
                if cleanup_error is None and not committed:
                    try:
                        await self._lifecycle.complete_settling(run)
                    except BaseException as exc:
                        remember(exc)
            finally:
                await stop_heartbeat(heartbeat)
            if cleanup_error is not None:
                raise cleanup_error

        cleanup_awaitable = cleanup()
        try:
            return self._cleanup.submit(cleanup_awaitable)
        except BaseException:
            cleanup_awaitable.close()
            raise

    def _execution_deadline(self, prepared: PreparedRun) -> float:
        """Shared Turn deadline from the deep execution context (P25)."""
        execution_runtime = getattr(prepared.execution, "execution", None)
        if execution_runtime is not None:
            return float(execution_runtime.deadline)
        return asyncio.get_running_loop().time() + self._turn_timeout_seconds

    @staticmethod
    def _trace_request(prepared: PreparedRun) -> str:
        # RLMExecutionContext.session (SessionView) owns the public request;
        # the fallback only protects legacy PreparedRun doubles.
        session = getattr(prepared.execution, "session", None)
        request = getattr(session, "request", "") if session is not None else ""
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
        receipt: RunSettlement,
    ) -> None:
        if isinstance(receipt, FailedRunReceipt) and outcome.succeeded:
            annotate_trace_io(
                request=trace_request,
                response_text=receipt.public_message or "Turn failed",
                failed=True,
            )


__all__ = [
    "RunEventStream",
    "RunExecutionDriver",
    "RunRunner",
    "_with_trace_id",
    "terminal",
]
