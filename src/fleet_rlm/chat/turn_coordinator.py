"""Turn use case: begin, prepare, execute, settle, project, and close."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Self
from uuid import UUID

from fleet_rlm.chat.commands import OpenTurnCommand
from fleet_rlm.chat.committed_turn_events import CommittedTurnEventProjector
from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor, TurnCleanupUnavailableError
from fleet_rlm.chat.turn_execution import (
    TurnEventStream,  # noqa: F401 - compatibility export
    TurnExecutionDriver,
    TurnRunner,
    _ClaimHeartbeat,
    _shield_cleanup,
    _stop_heartbeat,
    _terminal,  # noqa: F401 - compatibility export
    _with_trace_id,  # noqa: F401 - compatibility export
)
from fleet_rlm.chat.turn_lifecycle import (
    BeginTurn,
    ExecuteTurn,
    FailedRunReceipt,
    ReplayTurn,
    TurnFailure,
    TurnLifecycle,
    TurnLifecycleUnavailableError,
    TurnStateError,
)
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    TurnPreparation,
    TurnPreparationCancelledError,
    TurnPreparationTimeoutError,
)
from fleet_rlm.observability.turn_tracing import annotate_trace_io, turn_phase_span, turn_trace
from fleet_rlm.rlm.dspy_contract import empty_rlm_usage
from fleet_rlm.rlm.events import EventRecorder, RunCompleted, RunStarted, RuntimeEvent, Status


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
        lifecycle: TurnLifecycle,
        preparation: TurnPreparation,
        runner: TurnRunner,
        projector: CommittedTurnEventProjector | None = None,
        turn_timeout_seconds: int | float = 1800,
        cleanup: TurnCleanupSupervisor | None = None,
        claim_loss_fence: Callable[[UUID], Awaitable[None]] | None = None,
        mlflow_tracing_enabled: bool = False,
        mlflow_expose_trace_id: bool = True,
    ) -> None:
        self._lifecycle = lifecycle
        self._preparation = preparation
        self._runner = runner
        self._projector = projector or CommittedTurnEventProjector()
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._cleanup = cleanup or TurnCleanupSupervisor()
        self._claim_loss_fence = claim_loss_fence
        self._mlflow_tracing_enabled = mlflow_tracing_enabled
        self._mlflow_expose_trace_id = mlflow_expose_trace_id
        self._execution_driver = TurnExecutionDriver(
            lifecycle=lifecycle,
            runner=runner,
            projector=self._projector,
            cleanup=self._cleanup,
            claim_loss_fence=claim_loss_fence,
            turn_timeout_seconds=self._turn_timeout_seconds,
            revoke_claim=self._revoke_claim,
        )

    async def _prepare_with_trace(self, start: ExecuteTurn, *, deadline: float) -> PreparedTurn:
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
        except TurnCleanupUnavailableError as exc:
            raise TurnLifecycleUnavailableError("Turn cleanup capacity is unavailable") from exc
        deadline = asyncio.get_running_loop().time() + self._turn_timeout_seconds
        request = BeginTurn(
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
            raise TurnPreparationTimeoutError("Turn preparation timed out") from None

        if isinstance(start, ReplayTurn):
            return OpenedTurnStream(start.run_id, self._replay(start))

        heartbeat = self._start_heartbeat(start)

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
                preparation_task.cancel()
                await asyncio.gather(preparation_task, return_exceptions=True)
                self._submit_claim_loss_cleanup(start, heartbeat)
                raise TurnLifecycleUnavailableError("Turn claim is no longer available")
            prepared = preparation_task.result()
            if heartbeat_lost is not None:
                heartbeat_lost.cancel()
                await asyncio.gather(heartbeat_lost, return_exceptions=True)
        except TurnPreparationCancelledError:
            await _stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("cancelled", "cancelled", "Turn cancelled", empty_rlm_usage()),
                )
            )
            raise
        except (TurnPreparationTimeoutError, TimeoutError) as exc:
            await _stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure("timeout", "timeout", "Turn preparation timed out", empty_rlm_usage()),
                )
            )
            if isinstance(exc, TurnPreparationTimeoutError):
                raise
            raise TurnPreparationTimeoutError("Turn preparation timed out") from None
        except Exception:
            if start.authority.revoked:
                raise
            await _stop_heartbeat(heartbeat)
            await _shield_cleanup(
                self._lifecycle.finish(
                    start,
                    TurnFailure(
                        "failed",
                        "preparation_failed",
                        "Turn could not be prepared",
                        empty_rlm_usage(),
                    ),
                )
            )
            raise
        return OpenedTurnStream(start.run_id, self._execute(start, prepared, heartbeat))

    async def _replay(self, start: ReplayTurn) -> AsyncGenerator[RuntimeEvent]:
        recorder = EventRecorder(start.run_id, start.session_id)
        yield recorder.record(RunStarted(delivery="replay"))
        yield recorder.record(Status("replay", "running", "idempotent replay"))
        for event in self._projector.project(start.committed_turn, recorder, mode="replay"):
            yield event
        yield recorder.record(RunCompleted(checkpoint_version=start.checkpoint_version, delivery="replay"))

    async def _execute(
        self,
        turn: ExecuteTurn,
        prepared: PreparedTurn,
        heartbeat: _ClaimHeartbeat | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        with turn_trace(
            turn.session_id,
            turn.run_id,
            enabled=self._mlflow_tracing_enabled,
            expose_trace_id=self._mlflow_expose_trace_id,
        ) as handle:
            async for event in self._execution_driver.stream(turn, prepared, heartbeat, trace_id=handle.trace_id):
                yield event

    def _start_heartbeat(self, turn: ExecuteTurn) -> _ClaimHeartbeat | None:
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
                        await self._lifecycle.heartbeat(turn)
                except (TurnLifecycleUnavailableError, TurnStateError):
                    turn.authority.revoke()
                    lost.set()
                    return
                except Exception:  # transient persistence failure
                    now = loop.time()
                    if now >= authority_deadline:
                        turn.authority.revoke()
                        lost.set()
                        return
                    next_attempt = min(authority_deadline, now + min(interval, 1.0))
                else:
                    last_success = loop.time()
                    authority_deadline = last_success + stale_after - interval
                    next_attempt = last_success + interval

        return _ClaimHeartbeat(asyncio.create_task(maintain_claim(), name="fleet-turn-heartbeat"), lost)

    def _submit_claim_loss_cleanup(self, turn: ExecuteTurn, heartbeat: _ClaimHeartbeat) -> None:
        async def cleanup() -> None:
            try:
                await self._revoke_claim(turn, empty_rlm_usage())
                if self._claim_loss_fence is not None:
                    await self._claim_loss_fence(turn.session_id)
                await self._lifecycle.complete_settling(turn)
            finally:
                await _stop_heartbeat(heartbeat)

        self._cleanup.submit(cleanup())

    async def _revoke_claim(self, turn: ExecuteTurn, usage) -> FailedRunReceipt:
        failure = TurnFailure("failed", "stale_claim", "Turn failed", usage)
        return await self._lifecycle.revoke_claim(turn, failure)
