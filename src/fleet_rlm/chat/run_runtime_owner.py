"""Deep runtime ownership for one claimed Run (P21).

This module owns the transport-independent lifetime handle around the existing
RunLifecycle SQL authority and RLMRunner execution adapter. It deliberately
contains no FastAPI or DSPy imports. The handle owns the open task, prepared
stream, close task, cancellation shielding, and an internal terminal receipt;
callers may project events but may not settle owned resources themselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Self

from fleet_rlm.chat.run_ownership import shield_cleanup


class RunOwnershipTransitionError(RuntimeError):
    """An invalid or duplicate local Run ownership transition."""


class RunOwnershipState(StrEnum):
    """Local runtime states; these are intentionally not SQL row statuses."""

    CLAIMED = "claimed"
    PREPARING = "preparing"
    PREPARED = "prepared"
    EXECUTING = "executing"
    FINALIZING = "finalizing"
    SETTLING = "settling"
    QUARANTINED = "quarantined"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class OwnershipComponentReceipt:
    """Bounded result for one owned runtime component."""

    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunLifetimeReceipt:
    """Internal aggregate terminal proof; never serialized onto SSE."""

    state: RunOwnershipState
    durable: Any | None
    root_worker: OwnershipComponentReceipt
    recursive_workers: OwnershipComponentReceipt
    provider: OwnershipComponentReceipt
    post_commit: OwnershipComponentReceipt
    quarantine: OwnershipComponentReceipt
    sandbox_leases: tuple[Any, ...] = ()
    cleanup_error: str | None = None

    @property
    def clean(self) -> bool:
        """Whether durable outcome and all owned cleanup reached a clean proof."""
        return (
            self.durable is not None
            and self.root_worker.status in {"clean", "not_applicable"}
            and self.recursive_workers.status in {"clean", "not_applicable"}
            and self.provider.status in {"clean", "not_applicable"}
            and self.post_commit.status in {"clean", "not_applicable"}
            and self.quarantine.status == "not_quarantined"
            and self.cleanup_error is None
        )


class _OpenedStream(Protocol):
    def __aiter__(self) -> Self: ...

    async def __anext__(self) -> Any: ...

    async def aclose(self) -> None: ...


OpenFactory = Callable[..., Awaitable[_OpenedStream]]

_ALLOWED: dict[RunOwnershipState, frozenset[RunOwnershipState]] = {
    RunOwnershipState.CLAIMED: frozenset({RunOwnershipState.PREPARING, RunOwnershipState.SETTLING}),
    RunOwnershipState.PREPARING: frozenset({RunOwnershipState.PREPARED, RunOwnershipState.SETTLING}),
    RunOwnershipState.PREPARED: frozenset({RunOwnershipState.EXECUTING, RunOwnershipState.SETTLING}),
    RunOwnershipState.EXECUTING: frozenset({RunOwnershipState.FINALIZING, RunOwnershipState.SETTLING}),
    RunOwnershipState.FINALIZING: frozenset({RunOwnershipState.SETTLING}),
    RunOwnershipState.SETTLING: frozenset({RunOwnershipState.QUARANTINED, RunOwnershipState.RELEASED}),
    RunOwnershipState.QUARANTINED: frozenset({RunOwnershipState.RELEASED}),
    RunOwnershipState.RELEASED: frozenset(),
}


class RunOwnership:
    """Own one claimed Run's open task, stream, and idempotent close.

    ``open_factory`` is the coordinator's existing claim/preparation/execution
    adapter. It remains responsible for durable SQL transitions and native RLM
    execution; this owner is responsible for retaining and settling the
    runtime operations exactly once. The factory receives a settlement callback
    so the aggregate receipt captures the durable outcome without importing
    either authority into this module.
    """

    def __init__(self, open_factory: OpenFactory) -> None:
        self._open_factory = open_factory
        self._state = RunOwnershipState.CLAIMED
        self._open_task: asyncio.Task[_OpenedStream] | None = None
        self._opened: _OpenedStream | None = None
        self._iterator: Any | None = None
        self._close_task: asyncio.Task[RunLifetimeReceipt] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._receipt: RunLifetimeReceipt | None = None
        self._durable: Any | None = None
        self._iter_started = False
        self._open_error: BaseException | None = None

    @property
    def state(self) -> RunOwnershipState:
        return self._state

    @property
    def receipt(self) -> RunLifetimeReceipt | None:
        return self._receipt

    def transition(self, target: RunOwnershipState) -> None:
        """Apply one deterministic local transition; duplicates fail closed."""
        if target is self._state:
            raise RunOwnershipTransitionError(f"duplicate Run ownership transition: {self._state.value}")
        if target not in _ALLOWED[self._state]:
            raise RunOwnershipTransitionError(
                f"invalid Run ownership transition: {self._state.value} -> {target.value}"
            )
        self._state = target

    def _record_durable(self, durable: Any) -> None:
        self._durable = durable
        if self._state is RunOwnershipState.EXECUTING:
            self.transition(RunOwnershipState.FINALIZING)

    def _record_cleanup(self, task: asyncio.Task[None]) -> None:
        """Retain detached cleanup until the owner proves it settled."""
        if self._cleanup_task is not None and self._cleanup_task is not task:
            raise RunOwnershipTransitionError("duplicate Run cleanup task")
        self._cleanup_task = task

    def start(self) -> Self:
        """Start the coordinator-owned open task exactly once."""
        if self._open_task is not None:
            raise RunOwnershipTransitionError("Run ownership open already started")

        async def open_owned() -> _OpenedStream:
            self.transition(RunOwnershipState.PREPARING)
            try:
                opened = await self._open_factory(self._record_durable, self._record_cleanup)
            except BaseException as exc:
                self._open_error = exc
                if self._state not in {RunOwnershipState.SETTLING, RunOwnershipState.RELEASED}:
                    self.transition(RunOwnershipState.SETTLING)
                raise
            self._opened = opened
            self._iterator = opened.__aiter__()
            if self._state is RunOwnershipState.PREPARING:
                self.transition(RunOwnershipState.PREPARED)
            return opened

        self._open_task = asyncio.create_task(open_owned(), name="fleet-run-ownership-open")
        return self

    async def wait_open(self, *, timeout: float | None = None) -> _OpenedStream | None:
        """Wait for preparation without cancelling the owner task on timeout."""
        task = self._open_task
        if task is None:
            raise RunOwnershipTransitionError("Run ownership open has not started")
        if task.done():
            return task.result()
        if timeout is None:
            return await asyncio.shield(task)
        done, _ = await asyncio.wait({task}, timeout=max(0.0, timeout))
        if not done:
            return None
        return task.result()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Any:
        opened = self._opened
        if opened is None:
            opened = await self.wait_open()
        if not self._iter_started:
            self._iter_started = True
            if self._state is RunOwnershipState.PREPARED:
                self.transition(RunOwnershipState.EXECUTING)
        assert opened is not None
        iterator = self._iterator or opened.__aiter__()
        self._iterator = iterator
        return await iterator.__anext__()

    async def _close_owned(self) -> RunLifetimeReceipt:
        cleanup_error: BaseException | None = None
        opened = self._opened
        try:
            if opened is None and self._open_task is not None:
                try:
                    opened = await shield_cleanup(self._open_task)
                    self._opened = opened
                except BaseException as exc:
                    cleanup_error = exc
            if self._state not in {RunOwnershipState.SETTLING, RunOwnershipState.RELEASED}:
                self.transition(RunOwnershipState.SETTLING)
            if opened is not None:
                # An opened async generator does not execute its body/finally
                # when closed before first iteration. Prime it here so the
                # coordinator's execution finally owns provider/worker cleanup.
                if not self._iter_started:
                    self._iter_started = True
                    if self._state is RunOwnershipState.SETTLING:
                        # PREPARED -> SETTLING already happened above.
                        pass
                    iterator = self._iterator or opened.__aiter__()
                    self._iterator = iterator
                    with_context = iterator.__anext__()
                    try:
                        await shield_cleanup(with_context)
                    except (StopAsyncIteration, asyncio.CancelledError):
                        pass
                    except BaseException as exc:
                        cleanup_error = cleanup_error or exc
                try:
                    await shield_cleanup(opened.aclose())
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if self._cleanup_task is not None:
                try:
                    await shield_cleanup(self._cleanup_task)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
        except BaseException as exc:
            cleanup_error = cleanup_error or exc

        resource_receipt = getattr(opened, "cleanup_receipt", None) if opened is not None else None
        if resource_receipt is not None and not getattr(resource_receipt, "clean", False):
            cleanup_error = cleanup_error or RuntimeError("prepared resource cleanup reported failure")
        if cleanup_error is None:
            self.transition(RunOwnershipState.RELEASED)
            state = RunOwnershipState.RELEASED
            quarantine = OwnershipComponentReceipt("not_quarantined")
        else:
            self.transition(RunOwnershipState.QUARANTINED)
            state = RunOwnershipState.QUARANTINED
            quarantine = OwnershipComponentReceipt("quarantined", _safe_error(cleanup_error))
        resource_status = "clean" if resource_receipt is None or getattr(resource_receipt, "clean", False) else "failed"
        lease_receipts = tuple(
            result
            for result in getattr(resource_receipt, "results", ())
            if hasattr(result, "provider") and hasattr(result, "admission")
        )
        receipt = RunLifetimeReceipt(
            state=state,
            durable=self._durable,
            root_worker=OwnershipComponentReceipt(
                "clean" if cleanup_error is None else "failed", _safe_error(cleanup_error) if cleanup_error else None
            ),
            recursive_workers=OwnershipComponentReceipt(resource_status),
            provider=OwnershipComponentReceipt(resource_status),
            post_commit=OwnershipComponentReceipt("clean" if cleanup_error is None else "unknown"),
            quarantine=quarantine,
            sandbox_leases=lease_receipts,
            cleanup_error=_safe_error(cleanup_error) if cleanup_error else None,
        )
        self._receipt = receipt
        return receipt

    async def aclose(self) -> RunLifetimeReceipt:
        """Close once, through one cancellation-resistant close task."""
        if self._receipt is not None:
            return self._receipt
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(), name="fleet-run-ownership-close")
        return await shield_cleanup(self._close_task)


def _safe_error(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    return f"{type(exc).__name__}: {str(exc)[:200]}"


__all__ = [
    "OwnershipComponentReceipt",
    "RunLifetimeReceipt",
    "RunOwnership",
    "RunOwnershipState",
    "RunOwnershipTransitionError",
]
