"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any, Self
from uuid import UUID

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor, RunCleanupUnavailableError
from fleet_rlm.chat.run_execution import (
    RunExecutionDriver,
    RunRunner,
    _ClaimHeartbeat,
    _consume_task_exception,
    _shield_cleanup,
    _stop_heartbeat,
    _terminal,  # noqa: F401 - compatibility export
)
from fleet_rlm.chat.run_lifecycle import (
    ClaimedRun,
    CommittedRunReplay,
    FailedRunReceipt,
    RunAlreadyCompletedError,
    RunClaim,
    RunFailure,
    RunLifecycle,
    RunLifecycleUnavailableError,
    RunStateError,
)
from fleet_rlm.chat.run_preparation import (
    PreparedRun,
    RunPreparation,
    RunPreparationCancelledError,
    RunPreparationTimeoutError,
)
from fleet_rlm.observability.turn_tracing import annotate_trace_io, turn_phase_span, turn_trace
from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, RuntimeEvent, Status

logger = logging.getLogger(__name__)

_PREPARATION_CLEANUP_TIMEOUT_S = 1.0


async def _wait_late_preparation_task(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(BaseException):
        await task


async def _shield_cleanup_result(awaitable: Awaitable[Any]) -> Any:
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if task.cancelled():
        raise asyncio.CancelledError
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class OpenedTurnStream:
    """Prepared stream handle whose close shields settlement and cleanup."""

    def __init__(self, run_id: UUID, events: AsyncIterator[RuntimeEvent]) -> None:
        self.run_id = run_id
        self._events = events

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        return await self._events.__anext__()

    async def aclose(self) -> None:
        close = getattr(self._events, "aclose", None)
        if close is not None:
            await _shield_cleanup(close())


class TurnCoordinator:
    """Own public delivery ordering while domain modules own state and resources."""

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
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._cleanup = cleanup or RunCleanupSupervisor()
        self._claim_loss_fence = claim_loss_fence
        self._mlflow_tracing_enabled = mlflow_tracing_enabled
        self._mlflow_expose_trace_id = mlflow_expose_trace_id
        self._execution_driver = RunExecutionDriver(
            lifecycle=lifecycle,
            runner=runner,
            projector=self._projector,
            cleanup=self._cleanup,
            claim_loss_fence=claim_loss_fence,
            turn_timeout_seconds=self._turn_timeout_seconds,
            revoke_claim=self._revoke_claim,
        )

    async def _prepare_with_trace(self, start: ClaimedRun, *, deadline: float) -> PreparedRun:
        """Trace preparation separately because SSE begins only after it succeeds."""
        with turn_trace(
            start.session_id,
            start.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=False,
        ):
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
            return prepared

    async def open(self, command: OpenTurnCommand) -> OpenedTurnStream:
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
        preparation_task: asyncio.Task[PreparedRun] | None = None
        heartbeat_lost: asyncio.Task[bool] | None = None
        preparation_quarantine: set[asyncio.Task[Any]] = set()
        preparation_cleanup_error: BaseException | None = None

        def record_preparation_cleanup_error(exc: BaseException) -> None:
            nonlocal preparation_cleanup_error
            if preparation_cleanup_error is None:
                preparation_cleanup_error = exc

        def claim_lost() -> bool:
            return heartbeat is not None and (heartbeat.lost.is_set() or start.authority.revoked)

        async def drain_late_preparation(task: asyncio.Task[PreparedRun]) -> None:
            try:
                late_prepared = await task
            except BaseException:
                return
            try:
                await _shield_cleanup(late_prepared.aclose())
            except BaseException as exc:
                record_preparation_cleanup_error(exc)
                logger.exception("late Turn preparation cleanup failed", extra={"run_id": str(start.run_id)})

        def retain_preparation_quarantine(task: asyncio.Task[Any]) -> None:
            preparation_quarantine.add(task)
            task.add_done_callback(preparation_quarantine.discard)

        async def cancel_preparation_tasks() -> bool:
            for task in (preparation_task, heartbeat_lost):
                if task is not None and not task.done():
                    task.cancel()
            tasks = tuple(task for task in (preparation_task, heartbeat_lost) if task is not None)
            if not tasks:
                return False
            done, pending = await asyncio.wait(tasks, timeout=_PREPARATION_CLEANUP_TIMEOUT_S)
            if preparation_task is not None and preparation_task in pending:
                quarantine = asyncio.create_task(
                    drain_late_preparation(preparation_task),
                    name="fleet-late-preparation-cleanup",
                )
                retain_preparation_quarantine(quarantine)
            if heartbeat_lost is not None and heartbeat_lost in pending:
                quarantine = asyncio.create_task(
                    _wait_late_preparation_task(heartbeat_lost),
                    name="fleet-late-heartbeat-cleanup",
                )
                retain_preparation_quarantine(quarantine)
            if preparation_task is not None and preparation_task in done and not preparation_task.cancelled():
                try:
                    late_prepared = preparation_task.result()
                except BaseException:
                    pass
                else:
                    try:
                        await _shield_cleanup(late_prepared.aclose())
                    except BaseException as exc:
                        record_preparation_cleanup_error(exc)
                        logger.exception("late Turn preparation cleanup failed", extra={"run_id": str(start.run_id)})
            return bool(pending) or preparation_cleanup_error is not None

        async def drain_preparation_quarantine() -> None:
            quarantine_tasks = tuple(preparation_quarantine)
            if quarantine_tasks:
                results = await asyncio.gather(*quarantine_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        record_preparation_cleanup_error(result)
            if preparation_cleanup_error is not None:
                raise RuntimeError("late Turn preparation cleanup failed") from preparation_cleanup_error

        async def drain_preparation_and_complete_settling() -> None:
            await drain_preparation_quarantine()
            await self._lifecycle.complete_settling(start)

        async def handoff_preparation_cleanup() -> None:
            cleanup = drain_preparation_and_complete_settling()
            try:
                self._cleanup.submit(cleanup)
            except BaseException:
                cleanup.close()
                await _shield_cleanup(drain_preparation_and_complete_settling())

        try:
            preparation = self._prepare_with_trace(start, deadline=deadline)
            preparation_task = asyncio.create_task(preparation)
            heartbeat_lost = asyncio.create_task(heartbeat.lost.wait()) if heartbeat is not None else None
            waiters = {preparation_task}
            if heartbeat_lost is not None:
                waiters.add(heartbeat_lost)
            async with asyncio.timeout_at(deadline):
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_lost is not None and heartbeat_lost in done:
                assert heartbeat is not None
                if not heartbeat.lost.is_set():
                    raise AssertionError("heartbeat loss waiter completed without claim loss")
                start.authority.revoke()
                preparation_pending = await _shield_cleanup_result(cancel_preparation_tasks())
                await self._submit_claim_loss_cleanup_or_drain(
                    start,
                    heartbeat,
                    preparation_cleanup=drain_preparation_quarantine if preparation_pending else None,
                )
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            prepared = preparation_task.result()
            if heartbeat_lost is not None:
                heartbeat_lost.cancel()
                await asyncio.gather(heartbeat_lost, return_exceptions=True)
        except (RunPreparationCancelledError, asyncio.CancelledError):
            claim_was_lost = heartbeat is not None and (heartbeat.lost.is_set() or start.authority.revoked)
            preparation_pending = await _shield_cleanup_result(cancel_preparation_tasks())
            claim_was_lost = claim_was_lost or (heartbeat is not None and heartbeat.lost.is_set())
            if claim_was_lost:
                assert heartbeat is not None
                await self._submit_claim_loss_cleanup_or_drain(
                    start,
                    heartbeat,
                    preparation_cleanup=drain_preparation_quarantine if preparation_pending else None,
                )
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            await _stop_heartbeat(heartbeat)
            failure = RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage())
            if preparation_pending:
                start.authority.revoke()
                try:
                    await _shield_cleanup(self._lifecycle.settle(start, failure))
                finally:
                    await _shield_cleanup(handoff_preparation_cleanup())
            else:
                try:
                    await _shield_cleanup(self._lifecycle.finish(start, failure))
                finally:
                    start.authority.revoke()
            raise
        except (RunPreparationTimeoutError, TimeoutError) as exc:
            claim_was_lost = heartbeat is not None and (heartbeat.lost.is_set() or start.authority.revoked)
            preparation_pending = await _shield_cleanup_result(cancel_preparation_tasks())
            claim_was_lost = claim_was_lost or (heartbeat is not None and heartbeat.lost.is_set())
            if claim_was_lost:
                assert heartbeat is not None
                await self._submit_claim_loss_cleanup_or_drain(
                    start,
                    heartbeat,
                    preparation_cleanup=drain_preparation_quarantine if preparation_pending else None,
                )
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            await _stop_heartbeat(heartbeat)
            failure = RunFailure("timeout", "timeout", "Turn preparation timed out", empty_rlm_usage())
            if preparation_pending:
                start.authority.revoke()
                try:
                    await _shield_cleanup(self._lifecycle.settle(start, failure))
                finally:
                    await _shield_cleanup(handoff_preparation_cleanup())
            else:
                try:
                    await _shield_cleanup(self._lifecycle.finish(start, failure))
                finally:
                    start.authority.revoke()
            if isinstance(exc, RunPreparationTimeoutError):
                raise
            raise RunPreparationTimeoutError("Turn preparation timed out") from None
        except Exception:
            preparation_pending = await _shield_cleanup_result(cancel_preparation_tasks())
            if claim_lost():
                assert heartbeat is not None
                await self._submit_claim_loss_cleanup_or_drain(
                    start,
                    heartbeat,
                    preparation_cleanup=drain_preparation_quarantine if preparation_pending else None,
                )
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            await _stop_heartbeat(heartbeat)
            failure = RunFailure(
                "failed",
                "preparation_failed",
                "Turn could not be prepared",
                empty_rlm_usage(),
            )
            if preparation_pending:
                start.authority.revoke()
                try:
                    await _shield_cleanup(self._lifecycle.settle(start, failure))
                finally:
                    await _shield_cleanup(handoff_preparation_cleanup())
            else:
                await _shield_cleanup(self._lifecycle.finish(start, failure))
            raise
        return OpenedTurnStream(start.run_id, self._execute(start, prepared, heartbeat))

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
        prepared: PreparedRun,
        heartbeat: _ClaimHeartbeat | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        with turn_trace(
            run.session_id,
            run.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=self._mlflow_expose_trace_id,
        ) as handle:
            async for event in self._execution_driver.stream(run, prepared, heartbeat, trace_id=handle.trace_id):
                yield event

    def _start_heartbeat(self, run: ClaimedRun) -> _ClaimHeartbeat | None:
        interval = max(0.01, float(self._lifecycle.heartbeat_seconds))
        stale_after = max(interval * 3, float(self._lifecycle.stale_after_seconds))
        lost = asyncio.Event()

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

        return _ClaimHeartbeat(asyncio.create_task(maintain_claim(), name="fleet-turn-heartbeat"), lost)

    async def _claim_loss_cleanup(
        self,
        run: ClaimedRun,
        heartbeat: _ClaimHeartbeat,
        preparation_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        receipt: FailedRunReceipt | None = None
        try:
            await _stop_heartbeat(heartbeat)
            try:
                receipt = await self._revoke_claim(run, empty_rlm_usage())
                if receipt is not None and self._claim_loss_fence is not None:
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
            await _stop_heartbeat(heartbeat)

    def _submit_claim_loss_cleanup(self, run: ClaimedRun, heartbeat: _ClaimHeartbeat) -> None:
        """Submit claim-loss cleanup for compatibility with synchronous callers."""
        cleanup_awaitable = self._claim_loss_cleanup(run, heartbeat)
        try:
            self._cleanup.submit(cleanup_awaitable)
        except BaseException:
            cleanup_awaitable.close()
            task = asyncio.create_task(self._claim_loss_cleanup(run, heartbeat), name="fleet-claim-loss-cleanup")
            task.add_done_callback(_consume_task_exception)

    async def _submit_claim_loss_cleanup_or_drain(
        self,
        run: ClaimedRun,
        heartbeat: _ClaimHeartbeat,
        *,
        preparation_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        cleanup_awaitable = self._claim_loss_cleanup(run, heartbeat, preparation_cleanup)
        try:
            self._cleanup.submit(cleanup_awaitable)
        except BaseException:
            cleanup_awaitable.close()
            with contextlib.suppress(BaseException):
                await _shield_cleanup(self._claim_loss_cleanup(run, heartbeat, preparation_cleanup))

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
