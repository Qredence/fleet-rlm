"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, Self, TypeAlias
from uuid import UUID

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.preparation import (
    PreparedTurn,
    RunPreparation,
    RunPreparationCancelledError,
    RunPreparationTimeoutError,
)
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedRunReplay,
    CommittedTurnReceipt,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunClaim,
    RunFailure,
    RunLifecycle,
    RunLifecycleUnavailableError,
    RunSettlement,
    RunStateError,
)
from fleet_rlm.chat.run_ownership import (
    ClaimHeartbeat,
    shield_cleanup,
    stop_heartbeat,
)
from fleet_rlm.observability.tracing import annotate_trace_io, turn_phase_span, turn_trace
from fleet_rlm.rlm.events import (
    PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE,
    TERMINAL_DETAIL_TYPES,
    EventRecorder,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunFailedMessage,
    RunStarted,
    RunTimedOut,
    RuntimeEvent,
    Status,
)
from fleet_rlm.rlm.result import RLMOutcome, RLMUsage, empty_rlm_usage
from fleet_rlm.rlm.runtime import RLMExecutionContext
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor, RunCleanupUnavailableError

logger = logging.getLogger(__name__)


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


class OpenedTurnStream:
    """TurnRuntime-owned stream handle with cancellation-resistant close."""

    def __init__(
        self,
        run_id: UUID | None,
        events: AsyncIterator[RuntimeEvent] | None = None,
        *,
        prepared: PreparedTurn | None = None,
        open_task: asyncio.Task[OpenedTurnStream] | None = None,
    ) -> None:
        self.run_id = run_id
        self._events = events
        self._prepared = prepared
        self._open_task = open_task
        self._opened_owner: OpenedTurnStream | None = None
        self._iterator: AsyncIterator[RuntimeEvent] | None = events
        self._opened_resource: Any | None = None
        self._iter_started = False
        self._close_task: asyncio.Task[None] | None = None
        self._open_error: BaseException | None = None

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        await self._resolve_open()
        if self._opened_owner is not None:
            return await self._opened_owner.__anext__()
        if self._iterator is None:
            raise StopAsyncIteration
        self._iter_started = True
        return await self._iterator.__anext__()

    async def wait_open(self, *, timeout: float | None = None) -> OpenedTurnStream | None:
        """Wait for TurnRuntime preparation without cancelling its owned task."""
        if self._events is not None:
            return self
        if self._open_task is None and self._opened_owner is None:
            return self
        try:
            if timeout is None:
                await self._resolve_open()
            else:
                async with asyncio.timeout(max(0.0, timeout)):
                    await self._resolve_open()
        except TimeoutError:
            return None
        return self

    async def _resolve_open(self) -> None:
        if self._opened_owner is not None:
            await self._opened_owner.wait_open()
            return
        if self._events is not None or self._open_task is None:
            return
        try:
            opened = await asyncio.shield(self._open_task)
        except BaseException as exc:
            self._open_error = exc
            raise
        if isinstance(opened, OpenedTurnStream):
            self.run_id = opened.run_id
            self._opened_owner = opened
            await opened.wait_open()
            self.run_id = opened.run_id
            return
        self.run_id = getattr(opened, "run_id", None)
        self._opened_resource = opened
        self._events = opened
        self._iterator = opened.__aiter__()

    async def _close_owned(self) -> None:
        try:
            await self._resolve_open()
        except BaseException:
            # The caller's open path owns the original failure. There are no
            # prepared resources to close when opening never produced a stream.
            if self._events is None:
                return
            raise
        if self._opened_owner is not None:
            await self._opened_owner.aclose()
            return
        if self._iterator is None:
            return
        close_error: BaseException | None = None

        def remember(exc: BaseException) -> None:
            nonlocal close_error
            if close_error is None:
                close_error = exc

        if not self._iter_started:
            self._iter_started = True
            try:
                await shield_cleanup(self._iterator.__anext__())
            except StopAsyncIteration:
                pass
            except BaseException as exc:
                remember(exc)
        close = getattr(self._iterator, "aclose", None)
        if close is not None:
            try:
                await shield_cleanup(close())
            except BaseException as exc:
                remember(exc)
        opened_close = getattr(self._opened_resource, "aclose", None)
        if opened_close is not None and self._opened_resource is not self._iterator:
            try:
                await shield_cleanup(opened_close())
            except BaseException as exc:
                remember(exc)
        if close_error is not None:
            raise close_error

    async def aclose(self) -> None:
        """Close once and shield the complete TurnRuntime settlement."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(), name="fleet-turn-stream-close")
        await shield_cleanup(self._close_task)

    @property
    def outcome(self) -> RLMOutcome | None:
        if self._opened_owner is not None:
            return self._opened_owner.outcome
        return getattr(self._events, "outcome", None)

    async def wait_owned(self) -> None:
        await self._resolve_open()
        if self._opened_owner is not None:
            await self._opened_owner.wait_owned()


def _attach_preparation_trace_id(prepared: PreparedTurn, trace_id: str | None) -> PreparedTurn:
    """
    Attach a preparation trace identifier for internal phase correlation.

    Parameters:
        prepared (PreparedTurn): Prepared run to annotate.
        trace_id (str | None): Preparation trace identifier, if available.

    Returns:
        PreparedTurn: The annotated run, or the original run when no identifier is
        provided or annotation is unsupported.
    """
    if not trace_id:
        return prepared
    try:
        return replace(prepared, preparation_trace_id=trace_id)  # type: ignore[type-var]
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


def terminal(
    recorder: EventRecorder,
    receipt: CommittedTurnReceipt | FailedRunReceipt,
    *,
    trace_id: str | None = None,
) -> RuntimeEvent:
    """Project one durable settlement into the live terminal event."""
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
    public_message: RunFailedMessage
    if message == "Turn output is too large":
        public_message = "Turn output is too large"
    elif message == "Turn output is invalid":
        public_message = "Turn output is invalid"
    elif message == PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE:
        public_message = PROVIDER_ENDPOINT_NOT_FOUND_MESSAGE
    else:
        public_message = "Turn failed"
    return recorder.record(RunFailed(code="execution_failed", message=public_message))


async def _wait_stream_owned(stream: RunEventStream) -> None:
    wait_owned = getattr(stream, "wait_owned", None)
    if callable(wait_owned):
        await wait_owned()


async def _close_stream_owned(
    stream: RunEventStream | None,
    remember: Callable[[BaseException], None],
) -> None:
    """Close and wait for one provider stream while retaining the first failure."""
    if stream is None:
        return
    try:
        await stream.aclose()
    except BaseException as exc:
        remember(exc)
    try:
        await _wait_stream_owned(stream)
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


class TurnRuntime:
    """Own the complete claim, preparation, execution, settlement, and cleanup flow."""

    def __init__(
        self,
        *,
        lifecycle: RunLifecycle,
        preparation: RunPreparation,
        runner: RunRunner,
        projector: CommittedTurnEventProjector | None = None,
        turn_timeout_seconds: int | float = 1800,
        cleanup: RunCleanupSupervisor | None = None,
        claim_loss_fence: Callable[[UUID], Awaitable[None]] | None = None,
        mlflow_tracing_enabled: bool = False,
        mlflow_expose_trace_id: bool = True,
    ) -> None:
        """
        Initialize the TurnRuntime and its lifecycle dependencies.

        Parameters:
            lifecycle: Service for claiming, renewing, settling, and revoking runs.
            preparation: Service that prepares runs before execution.
            runner: Service that executes prepared runs.
            projector: Optional projector for committed turn events.
            turn_timeout_seconds: Maximum duration allowed for a turn.
            cleanup: Optional supervisor for asynchronous cleanup tasks.
            claim_loss_fence: Optional callback applied when claim loss requires fencing.
            mlflow_tracing_enabled: Whether MLflow tracing is enabled.
            mlflow_expose_trace_id: Whether trace IDs may be exposed.
        """
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._cleanup = cleanup or RunCleanupSupervisor()
        self._claim_loss_fence = claim_loss_fence
        self._mlflow_tracing_enabled = mlflow_tracing_enabled
        self._mlflow_expose_trace_id = mlflow_expose_trace_id

    async def _prepare_with_trace(self, start: ClaimedRun, *, deadline: float) -> PreparedTurn:
        """
        Prepare a claimed run while recording preparation tracing information for execution correlation.

        Parameters:
            start (ClaimedRun): The claimed run to prepare.
            deadline (float): Monotonic time by which preparation must complete.

        Returns:
            PreparedTurn: The prepared run, including its preparation trace identifier when supported.
        """
        with turn_trace(
            start.session_id,
            start.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=True,
            trace_phase="preparation",
        ) as handle:
            try:
                with turn_phase_span(
                    "Turn.prepare",
                    inputs={
                        "attachment_count": len(start.input.attachment_ids),
                        "skill_selection_count": len(start.input.skill_selections),
                    },
                ):
                    prepared = await self._preparation.prepare(start, deadline=deadline)
            except BaseException:
                annotate_trace_io(
                    request=start.input.text,
                    response_text="Turn preparation failed",
                    failed=True,
                )
                raise
            annotate_trace_io(request=start.input.text, response_text="Turn prepared")
            return _attach_preparation_trace_id(prepared, handle.trace_id)

    async def _prepare_claimed(
        self,
        run: ClaimedRun,
        heartbeat: ClaimHeartbeat | None,
        *,
        deadline: float,
    ) -> PreparedTurn:
        state = _PreparationState(run, heartbeat)
        state.preparation_task = asyncio.create_task(
            self._prepare_with_trace(run, deadline=deadline),
            name="fleet-turn-preparation",
        )
        state.heartbeat_lost = (
            asyncio.create_task(heartbeat.lost.wait(), name="fleet-turn-preparation-claim-loss")
            if heartbeat is not None
            else None
        )
        waiters: set[asyncio.Future[Any]] = {state.preparation_task}
        if state.heartbeat_lost is not None:
            waiters.add(state.heartbeat_lost)
        try:
            async with asyncio.timeout_at(deadline):
                done = {waiter for waiter in waiters if waiter.done()}
                if not done:
                    done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if state.heartbeat_lost is not None and state.heartbeat_lost in done:
                assert heartbeat is not None
                run.authority.revoke()
                preparation_pending = await shield_cleanup(self._cancel_preparation_tasks(state))
                await self._submit_claim_loss_cleanup_or_drain(
                    run,
                    heartbeat,
                    preparation_cleanup=(
                        (lambda: self._drain_preparation_quarantine(state)) if preparation_pending else None
                    ),
                )
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            assert state.preparation_task in done
            prepared = state.preparation_task.result()
        except (asyncio.CancelledError, RunPreparationCancelledError):
            await self._settle_preparation_failure(
                state,
                RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                cancel_like=True,
            )
            raise
        except (TimeoutError, RunPreparationTimeoutError):
            await self._settle_preparation_failure(
                state,
                RunFailure("timeout", "timeout", "Turn preparation timed out", empty_rlm_usage()),
                cancel_like=True,
            )
            raise RunPreparationTimeoutError("Turn preparation timed out") from None
        except RunLifecycleUnavailableError:
            raise
        except BaseException:
            await self._settle_preparation_failure(
                state,
                RunFailure(
                    "failed",
                    "preparation_failed",
                    "Turn could not be prepared",
                    empty_rlm_usage(),
                ),
                cancel_like=False,
            )
            raise
        await self._stop_preparation_claim_waiter(state)
        return prepared

    async def _settle_preparation_failure(
        self,
        state: _PreparationState,
        failure: RunFailure,
        *,
        cancel_like: bool,
    ) -> None:
        preparation_pending = await shield_cleanup(self._cancel_preparation_tasks(state))
        claim_lost = state.heartbeat is not None and (state.heartbeat.lost.is_set() or state.run.authority.revoked)
        if claim_lost:
            if state.heartbeat is None:
                raise RunLifecycleUnavailableError("Turn claim is no longer available")
            await self._submit_claim_loss_cleanup_or_drain(
                state.run,
                state.heartbeat,
                preparation_cleanup=(
                    (lambda: self._drain_preparation_quarantine(state)) if preparation_pending else None
                ),
            )
            raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
        await stop_heartbeat(state.heartbeat)
        if preparation_pending:
            state.run.authority.revoke()
            with contextlib.suppress(BaseException):
                await shield_cleanup(self._lifecycle.settle(state.run, failure))
            await shield_cleanup(self._handoff_preparation_cleanup(state.run, state))
            return
        if cancel_like:
            try:
                await shield_cleanup(self._lifecycle.finish(state.run, failure))
            finally:
                state.run.authority.revoke()
            return
        await shield_cleanup(self._lifecycle.finish(state.run, failure))

    async def _cancel_preparation_tasks(self, state: _PreparationState) -> bool:
        tasks = tuple(task for task in (state.preparation_task, state.heartbeat_lost) if task is not None)
        for task in tasks:
            if not task.done():
                task.cancel()
        if not tasks:
            return False
        done, pending = await asyncio.wait(tasks, timeout=_PREPARATION_CLEANUP_TIMEOUT_S)
        if state.preparation_task is not None and state.preparation_task in pending:
            quarantine = asyncio.create_task(
                self._drain_late_preparation(state, state.preparation_task),
                name="fleet-late-preparation-cleanup",
            )
            self._retain_preparation_quarantine(state, quarantine)
        if state.heartbeat_lost is not None and state.heartbeat_lost in pending:
            quarantine = asyncio.create_task(
                self._wait_late_task(state.heartbeat_lost),
                name="fleet-late-heartbeat-cleanup",
            )
            self._retain_preparation_quarantine(state, quarantine)
        if (
            state.preparation_task is not None
            and state.preparation_task in done
            and not state.preparation_task.cancelled()
        ):
            try:
                late_prepared = state.preparation_task.result()
            except BaseException:
                late_prepared = None
            if late_prepared is not None:
                try:
                    await shield_cleanup(late_prepared.aclose())
                except BaseException as exc:
                    self._record_preparation_cleanup_error(state, exc)
        return bool(pending) or state.cleanup_error is not None

    async def _drain_late_preparation(
        self,
        state: _PreparationState,
        task: asyncio.Task[PreparedTurn],
    ) -> None:
        try:
            prepared = await task
        except BaseException:
            return
        try:
            await shield_cleanup(prepared.aclose())
        except BaseException as exc:
            self._record_preparation_cleanup_error(state, exc)

    @staticmethod
    async def _wait_late_task(task: asyncio.Task[Any]) -> None:
        with contextlib.suppress(BaseException):
            await task

    @staticmethod
    def _retain_preparation_quarantine(state: _PreparationState, task: asyncio.Task[Any]) -> None:
        assert state.quarantine is not None
        state.quarantine.add(task)
        task.add_done_callback(state.quarantine.discard)

    @staticmethod
    def _record_preparation_cleanup_error(state: _PreparationState, exc: BaseException) -> None:
        if state.cleanup_error is None:
            state.cleanup_error = exc
        logger.error("late Turn preparation cleanup failed", exc_info=exc)

    async def _drain_preparation_quarantine(self, state: _PreparationState) -> None:
        assert state.quarantine is not None
        tasks = tuple(state.quarantine)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    self._record_preparation_cleanup_error(state, result)
        if state.cleanup_error is not None:
            raise RuntimeError("late Turn preparation cleanup failed") from state.cleanup_error

    async def _handoff_preparation_cleanup(self, run: ClaimedRun, state: _PreparationState) -> None:
        cleanup = self._drain_preparation_and_complete_settling(run, state)
        try:
            self._cleanup.submit(cleanup)
        except BaseException:
            cleanup.close()
            await shield_cleanup(self._drain_preparation_and_complete_settling(run, state))

    async def _drain_preparation_and_complete_settling(
        self,
        run: ClaimedRun,
        state: _PreparationState,
    ) -> None:
        await self._drain_preparation_quarantine(state)
        await self._lifecycle.complete_settling(run)

    async def _stop_preparation_claim_waiter(self, state: _PreparationState) -> None:
        waiter = state.heartbeat_lost
        if waiter is None:
            return
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        state.heartbeat_lost = None

    def open_owned(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Start claim-to-cleanup ownership without exposing a second owner object."""
        task = asyncio.create_task(self._open_impl(command), name="fleet-turn-open")
        return OpenedTurnStream(None, open_task=task)

    async def open(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Open and prepare a Turn before returning its event stream."""
        return await self._open_impl(command)

    async def _open_impl(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Complete claim and preparation before a transport sends headers."""
        try:
            self._cleanup.require_capacity()
        except RunCleanupUnavailableError as exc:
            raise RunLifecycleUnavailableError("Turn cleanup capacity is unavailable") from exc
        deadline = asyncio.get_running_loop().time() + self._turn_timeout_seconds
        request = RunClaim(
            command.access,
            command.session_id,
            command.input,
            command.idempotency_key,
            command.proposed_run_id,
        )
        try:
            async with asyncio.timeout_at(deadline):
                start = await self._lifecycle.begin(request)
        except TimeoutError:
            raise RunPreparationTimeoutError("Turn preparation timed out") from None

        if isinstance(start, CommittedRunReplay):
            return OpenedTurnStream(start.run_id, self._replay(start))

        heartbeat = self._start_heartbeat(start)
        prepared = await self._prepare_claimed(start, heartbeat, deadline=deadline)
        return OpenedTurnStream(
            start.run_id,
            self._execute(start, prepared, heartbeat),
            prepared=prepared,
        )

    async def _replay(self, start: CommittedRunReplay) -> AsyncGenerator[RuntimeEvent]:
        recorder = EventRecorder(start.run_id, start.session_id)
        yield recorder.record(RunStarted(delivery="replay"))
        yield recorder.record(Status("replay", "running", "idempotent replay"))
        for event in self._projector.project(start.committed_turn, recorder, mode="replay"):
            yield event
        yield recorder.record(RunCompleted(checkpoint_version=start.checkpoint_version, delivery="replay"))

    async def _execute(
        self,
        run: ClaimedRun,
        prepared: PreparedTurn,
        heartbeat: ClaimHeartbeat | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Stream runtime events for the prepared run during the execution phase.

        Parameters:
            run (ClaimedRun): The claimed run to execute.
            prepared (PreparedTurn): The prepared run configuration.
            heartbeat (ClaimHeartbeat | None): The heartbeat maintaining the run claim.
            on_settlement (Callable[[object], None] | None): Callback invoked when settlement occurs.
            on_cleanup (Callable[[asyncio.Task[None]], None] | None): Callback invoked when cleanup is scheduled.
        """
        with turn_trace(
            run.session_id,
            run.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=self._mlflow_expose_trace_id,
            trace_phase="execution",
            preparation_trace_id=getattr(prepared, "preparation_trace_id", None),
        ) as handle:
            async for event in self._execute_claimed(run, prepared, heartbeat, trace_id=handle.trace_id):
                yield event

    async def _execute_claimed(
        self,
        run: ClaimedRun,
        prepared: PreparedTurn,
        heartbeat: ClaimHeartbeat | None,
        *,
        trace_id: str | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Drain provider events, settle the Run, and hand off owned cleanup."""
        state = _ExecutionState(
            recorder=EventRecorder(run.run_id, run.session_id),
            heartbeat=heartbeat,
            claim_loss_waiter=(
                asyncio.create_task(heartbeat.lost.wait(), name="fleet-turn-execution-claim-loss")
                if heartbeat is not None
                else None
            ),
        )
        trace_request = self._trace_request(prepared)
        try:
            state.stream = self._runner.stream(prepared.execution)
            _defer_stream_runtime(state.stream)
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
                _mark_stream_runtime(state.stream, committed=False)
                yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                return

            _mark_stream_runtime(state.stream, committed=isinstance(receipt, CommittedTurnReceipt))
            self._annotate_receipt(trace_request, outcome, receipt)
            state.settled = True
            if isinstance(receipt, CommittedTurnReceipt):
                for event in self._projector.project(receipt.committed_turn, state.recorder, mode="live_suffix"):
                    yield event
            yield terminal(state.recorder, receipt, trace_id=trace_id)
        except (GeneratorExit, asyncio.CancelledError):
            await self._settle_cancellation(run, prepared, state)
            raise
        except Exception:
            if not state.settled:
                if _heartbeat_claim_lost(state) or run.authority.revoked:
                    _mark_stream_runtime(state.stream, committed=False)
                    await self._handoff_cleanup_or_drain(
                        run,
                        prepared,
                        state,
                        claim_lost=True,
                        claim_loss_usage=empty_rlm_usage(),
                    )
                    state.settled = True
                    yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
                else:
                    receipt = await self._recover_failure(run, prepared, trace_request)
                    if receipt is not None:
                        state.settled = True
                        yield terminal(state.recorder, receipt, trace_id=trace_id)
                    else:
                        yield state.recorder.record(RunFailed(code="unavailable", message="Turn failed"))
        except BaseException:
            if not state.settled and (_heartbeat_claim_lost(state) or run.authority.revoked):
                _mark_stream_runtime(state.stream, committed=False)
                try:
                    await self._handoff_cleanup_or_drain(
                        run,
                        prepared,
                        state,
                        claim_lost=True,
                        claim_loss_usage=empty_rlm_usage(),
                    )
                except BaseException:
                    logger.exception(
                        "claim-loss cleanup failed after non-standard execution failure",
                        extra={"run_id": str(run.run_id), "session_id": str(run.session_id)},
                    )
            raise
        finally:
            await self._close_execution(prepared, state)

    async def _drain_events(
        self,
        run: ClaimedRun,
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
                _mark_stream_runtime(state.stream, committed=False)
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
        done = {waiter for waiter in waiters if waiter.done()}
        if not done:
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
        prepared: PreparedTurn,
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
        execution_deadline = self._execution_deadline(prepared)
        remaining = max(0.0, execution_deadline - asyncio.get_running_loop().time())
        result = await self._wait_for_finalization(
            state.finalization_task,
            state.claim_loss_waiter,
            remaining,
            is_authority_revoked=lambda: run.authority.revoked,
        )
        if isinstance(result, _ClaimLost):
            # Cleanup retains ownership of the started finalization task. A
            # claim-loss signal can win the waiter race after the durable
            # commit has already succeeded, so reconcile that task before
            # projecting a terminal event; otherwise a committed Turn would
            # be reported as a false failure.
            await self._handoff_cleanup_or_drain(
                run,
                prepared,
                state,
                claim_lost=True,
                claim_loss_usage=outcome.usage,
                finalization_task=state.finalization_task,
            )
            committed = await self._reconcile_finalization_after_claim_loss(state.finalization_task)
            if isinstance(committed, CommittedTurnReceipt):
                return committed
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
        done = {waiter for waiter in waiters if waiter.done()}
        if not done:
            done, _ = await asyncio.wait(
                waiters,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
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

    @staticmethod
    async def _reconcile_finalization_after_claim_loss(
        finalization_task: asyncio.Task[RunSettlement] | None,
    ) -> CommittedTurnReceipt | None:
        """Observe owned finalization after a claim-loss waiter wins.

        The cleanup supervisor remains the resource owner; this is a second
        shielded observer of the same task, not a replacement cleanup path.
        Only a durable commit changes the live projection from claim-loss
        failure to the committed suffix and one completion terminal.
        """
        if finalization_task is None:
            return None
        try:
            receipt = await shield_cleanup(finalization_task)
        except BaseException:
            return None
        return receipt if isinstance(receipt, CommittedTurnReceipt) else None

    async def _finish_with_trace(
        self,
        run: ClaimedRun,
        resolution: RLMOutcome | RunFailure,
        prepared: PreparedTurn,
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
        prepared: PreparedTurn,
        state: _ExecutionState,
    ) -> None:
        _mark_stream_runtime(state.stream, committed=False)
        if state.settled:
            return

        async def settle_owned_cancellation() -> None:
            run.authority.revoke()
            with contextlib.suppress(BaseException):
                await self._cancel_pending_event(state)
            claim_lost = _heartbeat_claim_lost(state)
            if not claim_lost:
                with contextlib.suppress(BaseException):
                    await self._lifecycle.settle(
                        run,
                        RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                    )
                claim_lost = _heartbeat_claim_lost(state)
            await self._handoff_cleanup_or_drain(
                run,
                prepared,
                state,
                claim_lost=claim_lost,
                claim_loss_usage=empty_rlm_usage() if claim_lost else None,
                finalization_task=state.finalization_task,
            )
            await asyncio.sleep(0)

        await shield_cleanup(settle_owned_cancellation())

    async def _recover_failure(
        self,
        run: ClaimedRun,
        prepared: PreparedTurn,
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
        prepared: PreparedTurn,
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
        await self._stop_claim_waiter(state)
        state.heartbeat = None

    async def _handoff_cleanup_or_drain(
        self,
        run: ClaimedRun,
        prepared: PreparedTurn,
        state: _ExecutionState,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
        finalization_task: asyncio.Task[RunSettlement] | None = None,
    ) -> None:
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
            if state.cleanup_handed_off:
                return
            state.cleanup_handed_off = True
            owned_heartbeat = state.heartbeat

            async def inline_cleanup() -> None:
                try:
                    with contextlib.suppress(BaseException):
                        await self._stop_claim_waiter(state)
                    with contextlib.suppress(BaseException):
                        await stop_heartbeat(owned_heartbeat)
                    state.heartbeat = None
                    await self._drain_owned_execution(
                        run,
                        prepared,
                        state.stream,
                        owned_heartbeat,
                        finalization_task,
                        claim_lost=claim_lost,
                        claim_loss_usage=claim_loss_usage,
                        late_claim_loss_window=False,
                    )
                finally:
                    with contextlib.suppress(BaseException):
                        await self._stop_claim_waiter(state)
                    with contextlib.suppress(BaseException):
                        await stop_heartbeat(owned_heartbeat)
                    state.heartbeat = None

            await shield_cleanup(inline_cleanup())

    async def _close_execution(self, prepared: PreparedTurn, state: _ExecutionState) -> None:
        with turn_phase_span("Turn.cleanup", inputs={"cleanup_owned": not state.cleanup_handed_off}):
            await self._cancel_pending_event(state)
            await self._stop_claim_waiter(state)
            if not state.cleanup_handed_off:
                cleanup_error: BaseException | None = None

                def remember(exc: BaseException) -> None:
                    nonlocal cleanup_error
                    if cleanup_error is None:
                        cleanup_error = exc

                try:
                    await _close_stream_owned(state.stream, remember)
                    try:
                        await shield_cleanup(prepared.aclose())
                    except BaseException as exc:
                        remember(exc)
                    if cleanup_error is not None:
                        _mark_stream_runtime(state.stream, committed=False)
                finally:
                    await _release_stream_runtime(state.stream, remember)
                    await stop_heartbeat(state.heartbeat)
                if cleanup_error is not None:
                    raise cleanup_error
            else:
                await stop_heartbeat(state.heartbeat)

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
        prepared: PreparedTurn,
        heartbeat: ClaimHeartbeat | None,
        finalization_task: asyncio.Task[RunSettlement] | None = None,
        *,
        claim_lost: bool = False,
        claim_loss_usage: RLMUsage | None = None,
    ) -> asyncio.Task[None]:
        async def cleanup() -> None:
            cleanup_error = await self._drain_owned_execution(
                run,
                prepared,
                stream,
                heartbeat,
                finalization_task,
                claim_lost=claim_lost,
                claim_loss_usage=claim_loss_usage,
                late_claim_loss_window=True,
            )
            if cleanup_error is not None:
                raise cleanup_error

        awaitable = cleanup()
        try:
            return self._cleanup.submit(awaitable)
        except BaseException:
            awaitable.close()
            raise

    async def _drain_owned_execution(
        self,
        run: ClaimedRun,
        prepared: PreparedTurn,
        stream: RunEventStream | None,
        heartbeat: ClaimHeartbeat | None,
        finalization_task: asyncio.Task[RunSettlement] | None,
        *,
        claim_lost: bool,
        claim_loss_usage: RLMUsage | None,
        late_claim_loss_window: bool,
    ) -> BaseException | None:
        cleanup_error: BaseException | None = None
        committed = False
        claim_cleanup_attempted = False
        effective_claim_lost = claim_lost

        def remember(exc: BaseException) -> None:
            nonlocal cleanup_error
            if cleanup_error is None:
                cleanup_error = exc

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
            committed = receipt is None
            if (
                receipt is not None
                and heartbeat is not None
                and heartbeat.definitive_loss
                and self._claim_loss_fence is not None
            ):
                try:
                    await self._claim_loss_fence(run.session_id)
                except BaseException as exc:
                    remember(exc)

        try:
            if heartbeat is not None and heartbeat.lost.is_set():
                effective_claim_lost = True
            if effective_claim_lost:
                await apply_claim_loss()
                await stop_heartbeat(heartbeat)
            await _close_stream_owned(stream, remember)
            if finalization_task is not None:
                with contextlib.suppress(BaseException):
                    await shield_cleanup(finalization_task)
            try:
                await prepared.aclose()
            except BaseException as exc:
                _mark_stream_runtime(stream, committed=False)
                remember(exc)
            await _release_stream_runtime(stream, remember)
            if (
                late_claim_loss_window
                and heartbeat is not None
                and heartbeat.lost.is_set()
                and not effective_claim_lost
            ):
                await apply_claim_loss()
            if cleanup_error is None and not committed:
                try:
                    await self._lifecycle.complete_settling(run)
                except BaseException as exc:
                    remember(exc)
        finally:
            await stop_heartbeat(heartbeat)
        return cleanup_error

    def _execution_deadline(self, prepared: PreparedTurn) -> float:
        execution_runtime = getattr(prepared.execution, "execution", None)
        if execution_runtime is not None:
            return float(execution_runtime.deadline)
        return asyncio.get_running_loop().time() + self._turn_timeout_seconds

    @staticmethod
    def _trace_request(prepared: PreparedTurn) -> str:
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

    def _start_heartbeat(self, run: ClaimedRun) -> ClaimHeartbeat | None:
        interval = max(0.01, float(self._lifecycle.heartbeat_seconds))
        stale_after = max(interval * 3, float(self._lifecycle.stale_after_seconds))
        lost = asyncio.Event()
        heartbeat: ClaimHeartbeat

        async def maintain_claim() -> None:
            loop = asyncio.get_running_loop()
            last_success = loop.time()
            next_attempt = last_success + interval
            authority_deadline = last_success + stale_after - interval
            while True:
                await asyncio.sleep(max(0.0, next_attempt - loop.time()))
                try:
                    async with asyncio.timeout_at(authority_deadline):
                        await self._lifecycle.heartbeat(run)
                except RunAlreadyCompletedError:
                    # The commit released the durable claim; the heartbeat must
                    # never classify its own committed Run as claim loss.
                    logger.info(
                        "claim heartbeat stopped after commit session_id=%s run_id=%s",
                        run.session_id,
                        run.run_id,
                    )
                    return
                except (RunLifecycleUnavailableError, RunStateError):
                    run.authority.revoke()
                    heartbeat.definitive_loss = True
                    lost.set()
                    return
                except Exception:  # transient persistence failure
                    now = loop.time()
                    if now >= authority_deadline:
                        run.authority.revoke()
                        lost.set()
                        return
                    next_attempt = min(authority_deadline, now + min(interval, 1.0))
                else:
                    last_success = loop.time()
                    authority_deadline = last_success + stale_after - interval
                    next_attempt = last_success + interval

        heartbeat = ClaimHeartbeat(asyncio.create_task(maintain_claim(), name="fleet-turn-heartbeat"), lost)
        return heartbeat

    async def _claim_loss_cleanup(
        self,
        run: ClaimedRun,
        heartbeat: ClaimHeartbeat,
        preparation_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        receipt: FailedRunReceipt | None = None
        try:
            await stop_heartbeat(heartbeat)
            try:
                receipt = await self._revoke_claim(run, empty_rlm_usage())
                if receipt is not None and heartbeat.definitive_loss and self._claim_loss_fence is not None:
                    await self._claim_loss_fence(run.session_id)
            finally:
                if preparation_cleanup is not None:
                    await preparation_cleanup()
            if receipt is None:
                # The Run committed before the revocation attempt: the
                # commit owns the terminal state, so there is nothing to
                # fence or release.
                return
            await self._lifecycle.complete_settling(run)
        finally:
            await stop_heartbeat(heartbeat)

    async def _submit_claim_loss_cleanup_or_drain(
        self,
        run: ClaimedRun,
        heartbeat: ClaimHeartbeat,
        *,
        preparation_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        cleanup_awaitable = self._claim_loss_cleanup(run, heartbeat, preparation_cleanup)
        try:
            self._cleanup.submit(cleanup_awaitable)
        except BaseException:
            cleanup_awaitable.close()
            try:
                await shield_cleanup(self._claim_loss_cleanup(run, heartbeat, preparation_cleanup))
            except BaseException:
                logger.exception(
                    "inline claim-loss cleanup failed; durable claim remains recovery-owned",
                    extra={"run_id": str(run.run_id), "session_id": str(run.session_id)},
                )

    async def _revoke_claim(self, run: ClaimedRun, usage) -> FailedRunReceipt | None:
        """Revoke the durable claim, or return None when the Run already committed.

        A racing commit always wins: revocation against a committed Run is a
        benign no-op logged at INFO instead of surfacing as a failure.
        """
        failure = RunFailure("failed", "stale_claim", "Turn failed", usage)
        try:
            return await self._lifecycle.revoke_claim(run, failure)
        except RunAlreadyCompletedError:
            logger.info(
                "stale-claim revocation skipped for committed Run session_id=%s run_id=%s",
                run.session_id,
                run.run_id,
            )
            return None
