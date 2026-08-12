"""Owned Turn preparation attempt: wait, cancel, quarantine, and settle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
from fleet_rlm.chat.run_lifecycle import ClaimedRun, RunFailure, RunLifecycle, RunLifecycleUnavailableError
from fleet_rlm.chat.run_ownership import ClaimHeartbeat, shield_cleanup, stop_heartbeat
from fleet_rlm.chat.run_preparation import PreparedRun

logger = logging.getLogger(__name__)

_PREPARATION_CLEANUP_TIMEOUT_S = 1.0


class _SubmitClaimLoss(Protocol):
    async def __call__(
        self,
        run: ClaimedRun,
        heartbeat: ClaimHeartbeat,
        *,
        preparation_cleanup: Callable[[], Awaitable[None]] | None = None,
    ) -> None: ...


async def _wait_late_preparation_task(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(BaseException):
        await task


class PreparationAttempt:
    """Own preparation task, claim-loss waiter, cancel/quarantine, and failure settlement."""

    def __init__(
        self,
        *,
        run: ClaimedRun,
        heartbeat: ClaimHeartbeat | None,
        prepare: Awaitable[PreparedRun],
        lifecycle: RunLifecycle,
        cleanup: RunCleanupSupervisor,
        deadline: float,
        submit_claim_loss: _SubmitClaimLoss,
    ) -> None:
        self._run = run
        self._heartbeat = heartbeat
        self._prepare = prepare
        self._lifecycle = lifecycle
        self._cleanup = cleanup
        self._deadline = deadline
        self._submit_claim_loss = submit_claim_loss
        self._preparation_task: asyncio.Task[PreparedRun] | None = None
        self._heartbeat_lost: asyncio.Task[bool] | None = None
        self._preparation_quarantine: set[asyncio.Task[Any]] = set()
        self._preparation_cleanup_error: BaseException | None = None

    def claim_lost(self) -> bool:
        return self._heartbeat is not None and (
            self._heartbeat.lost.is_set() or self._run.authority.revoked
        )

    async def wait(self) -> PreparedRun:
        self._preparation_task = asyncio.create_task(self._prepare)
        self._heartbeat_lost = (
            asyncio.create_task(self._heartbeat.lost.wait()) if self._heartbeat is not None else None
        )
        waiters = {self._preparation_task}
        if self._heartbeat_lost is not None:
            waiters.add(self._heartbeat_lost)
        async with asyncio.timeout_at(self._deadline):
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if self._heartbeat_lost is not None and self._heartbeat_lost in done:
            assert self._heartbeat is not None
            if not self._heartbeat.lost.is_set():
                raise AssertionError("heartbeat loss waiter completed without claim loss")
            self._run.authority.revoke()
            preparation_pending = await shield_cleanup(self.cancel())
            await self._submit_claim_loss_with_cleanup(preparation_pending)
            raise RunLifecycleUnavailableError("Turn claim is no longer available") from None
        prepared = self._preparation_task.result()
        if self._heartbeat_lost is not None:
            self._heartbeat_lost.cancel()
            await asyncio.gather(self._heartbeat_lost, return_exceptions=True)
        return prepared

    async def cancel(self) -> bool:
        for task in (self._preparation_task, self._heartbeat_lost):
            if task is not None and not task.done():
                task.cancel()
        tasks = tuple(task for task in (self._preparation_task, self._heartbeat_lost) if task is not None)
        if not tasks:
            return False
        done, pending = await asyncio.wait(tasks, timeout=_PREPARATION_CLEANUP_TIMEOUT_S)
        if self._preparation_task is not None and self._preparation_task in pending:
            quarantine = asyncio.create_task(
                self._drain_late_preparation(self._preparation_task),
                name="fleet-late-preparation-cleanup",
            )
            self._retain_preparation_quarantine(quarantine)
        if self._heartbeat_lost is not None and self._heartbeat_lost in pending:
            quarantine = asyncio.create_task(
                _wait_late_preparation_task(self._heartbeat_lost),
                name="fleet-late-heartbeat-cleanup",
            )
            self._retain_preparation_quarantine(quarantine)
        if (
            self._preparation_task is not None
            and self._preparation_task in done
            and not self._preparation_task.cancelled()
        ):
            try:
                late_prepared = self._preparation_task.result()
            except BaseException:
                pass
            else:
                try:
                    await shield_cleanup(late_prepared.aclose())
                except BaseException as exc:
                    self._record_preparation_cleanup_error(exc)
                    logger.exception(
                        "late Turn preparation cleanup failed",
                        extra={"run_id": str(self._run.run_id)},
                    )
        return bool(pending) or self._preparation_cleanup_error is not None

    async def cancel_and_settle(self, failure: RunFailure) -> Literal["claim_lost", "settled"]:
        """Cancel/timeout path: sample claim loss before and after cancel; revoke after finish."""
        claim_was_lost = self._heartbeat is not None and (
            self._heartbeat.lost.is_set() or self._run.authority.revoked
        )
        preparation_pending = await shield_cleanup(self.cancel())
        claim_was_lost = claim_was_lost or (
            self._heartbeat is not None and self._heartbeat.lost.is_set()
        )
        if claim_was_lost:
            assert self._heartbeat is not None
            await self._submit_claim_loss_with_cleanup(preparation_pending)
            return "claim_lost"
        await stop_heartbeat(self._heartbeat)
        await self._settle_failure(failure, preparation_pending, revoke_after_finish=True)
        return "settled"

    async def settle_failure(self, failure: RunFailure) -> Literal["claim_lost", "settled"]:
        """Generic Exception path: claim_lost() only; finish without finally-revoke when not pending."""
        preparation_pending = await shield_cleanup(self.cancel())
        if self.claim_lost():
            assert self._heartbeat is not None
            await self._submit_claim_loss_with_cleanup(preparation_pending)
            return "claim_lost"
        await stop_heartbeat(self._heartbeat)
        await self._settle_failure(failure, preparation_pending, revoke_after_finish=False)
        return "settled"

    def _record_preparation_cleanup_error(self, exc: BaseException) -> None:
        if self._preparation_cleanup_error is None:
            self._preparation_cleanup_error = exc

    def _retain_preparation_quarantine(self, task: asyncio.Task[Any]) -> None:
        self._preparation_quarantine.add(task)
        task.add_done_callback(self._preparation_quarantine.discard)

    async def _drain_late_preparation(self, task: asyncio.Task[PreparedRun]) -> None:
        try:
            late_prepared = await task
        except BaseException:
            return
        try:
            await shield_cleanup(late_prepared.aclose())
        except BaseException as exc:
            self._record_preparation_cleanup_error(exc)
            logger.exception(
                "late Turn preparation cleanup failed",
                extra={"run_id": str(self._run.run_id)},
            )

    async def drain_quarantine(self) -> None:
        quarantine_tasks = tuple(self._preparation_quarantine)
        if quarantine_tasks:
            results = await asyncio.gather(*quarantine_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    self._record_preparation_cleanup_error(result)
        if self._preparation_cleanup_error is not None:
            raise RuntimeError("late Turn preparation cleanup failed") from self._preparation_cleanup_error

    async def _drain_preparation_and_complete_settling(self) -> None:
        await self.drain_quarantine()
        await self._lifecycle.complete_settling(self._run)

    async def _handoff_preparation_cleanup(self) -> None:
        cleanup = self._drain_preparation_and_complete_settling()
        try:
            self._cleanup.submit(cleanup)
        except BaseException:
            cleanup.close()
            await shield_cleanup(self._drain_preparation_and_complete_settling())

    async def _submit_claim_loss_with_cleanup(self, preparation_pending: bool) -> None:
        assert self._heartbeat is not None
        await self._submit_claim_loss(
            self._run,
            self._heartbeat,
            preparation_cleanup=self.drain_quarantine if preparation_pending else None,
        )

    async def _settle_failure(
        self,
        failure: RunFailure,
        preparation_pending: bool,
        *,
        revoke_after_finish: bool,
    ) -> None:
        if preparation_pending:
            self._run.authority.revoke()
            try:
                await shield_cleanup(self._lifecycle.settle(self._run, failure))
            finally:
                await shield_cleanup(self._handoff_preparation_cleanup())
            return
        if revoke_after_finish:
            try:
                await shield_cleanup(self._lifecycle.finish(self._run, failure))
            finally:
                self._run.authority.revoke()
            return
        await shield_cleanup(self._lifecycle.finish(self._run, failure))


__all__ = [
    "PreparationAttempt",
]
