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
from fleet_rlm.chat.preparation_attempt import PreparationAttempt
from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor, RunCleanupUnavailableError
from fleet_rlm.chat.run_execution import RunExecutionDriver, RunRunner
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
from fleet_rlm.chat.run_ownership import (
    ClaimHeartbeat,
    shield_cleanup,
    stop_heartbeat,
)
from fleet_rlm.chat.run_preparation import (
    PreparedRun,
    RunPreparation,
    RunPreparationCancelledError,
    RunPreparationTimeoutError,
)
from fleet_rlm.chat.run_runtime_owner import RunOwnership
from fleet_rlm.observability.turn_tracing import annotate_trace_io, turn_phase_span, turn_trace
from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, RuntimeEvent, Status

logger = logging.getLogger(__name__)


class OpenedTurnStream:
    """Prepared stream handle whose close shields settlement and cleanup."""

    def __init__(
        self,
        run_id: UUID,
        events: AsyncIterator[RuntimeEvent],
        *,
        prepared: PreparedRun | None = None,
    ) -> None:
        self.run_id = run_id
        self._events = events
        self._prepared = prepared

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> RuntimeEvent:
        return await self._events.__anext__()

    @property
    def cleanup_receipt(self) -> Any | None:
        return getattr(self._prepared, "cleanup_receipt", None)

    async def aclose(self) -> None:
        close = getattr(self._events, "aclose", None)
        if close is not None:
            await shield_cleanup(close())


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

    def open_owned(self, command: OpenTurnCommand) -> RunOwnership:
        """Start one coordinator-owned Run lifetime handle (P21)."""
        return RunOwnership(
            lambda on_settlement, on_cleanup: self._open_impl(
                command, on_settlement=on_settlement, on_cleanup=on_cleanup
            )
        ).start()

    async def open(self, command: OpenTurnCommand) -> OpenedTurnStream:
        """Compatibility wrapper returning the existing prepared stream."""
        return await self._open_impl(command, on_settlement=None)

    async def _open_impl(
        self,
        command: OpenTurnCommand,
        *,
        on_settlement: Callable[[object], None] | None = None,
        on_cleanup: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> OpenedTurnStream:
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
        attempt = PreparationAttempt(
            run=start,
            heartbeat=heartbeat,
            prepare=self._prepare_with_trace(start, deadline=deadline),
            lifecycle=self._lifecycle,
            cleanup=self._cleanup,
            deadline=deadline,
            submit_claim_loss=self._submit_claim_loss_cleanup_or_drain,
        )
        try:
            prepared = await attempt.wait()
        except (RunPreparationCancelledError, asyncio.CancelledError):
            result = await attempt.cancel_and_settle(
                RunFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage())
            )
            if result == "claim_lost":
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            raise
        except (RunPreparationTimeoutError, TimeoutError) as exc:
            result = await attempt.cancel_and_settle(
                RunFailure("timeout", "timeout", "Turn preparation timed out", empty_rlm_usage())
            )
            if result == "claim_lost":
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            if isinstance(exc, RunPreparationTimeoutError):
                raise
            raise RunPreparationTimeoutError("Turn preparation timed out") from None
        except Exception:
            result = await attempt.settle_failure(
                RunFailure(
                    "failed",
                    "preparation_failed",
                    "Turn could not be prepared",
                    empty_rlm_usage(),
                )
            )
            if result == "claim_lost":
                raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
            raise
        return OpenedTurnStream(
            start.run_id,
            self._execute(
                start,
                prepared,
                heartbeat,
                on_settlement=on_settlement,
                on_cleanup=on_cleanup,
            ),
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
        prepared: PreparedRun,
        heartbeat: ClaimHeartbeat | None,
        *,
        on_settlement: Callable[[object], None] | None = None,
        on_cleanup: Callable[[asyncio.Task[None]], None] | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        with turn_trace(
            run.session_id,
            run.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=self._mlflow_expose_trace_id,
        ) as handle:
            async for event in self._execution_driver.stream(
                run,
                prepared,
                heartbeat,
                trace_id=handle.trace_id,
                on_settlement=on_settlement,
                on_cleanup=on_cleanup,
            ):
                yield event

    def _start_heartbeat(self, run: ClaimedRun) -> ClaimHeartbeat | None:
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

        return ClaimHeartbeat(asyncio.create_task(maintain_claim(), name="fleet-turn-heartbeat"), lost)

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
            with contextlib.suppress(BaseException):
                await shield_cleanup(self._claim_loss_cleanup(run, heartbeat, preparation_cleanup))

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
