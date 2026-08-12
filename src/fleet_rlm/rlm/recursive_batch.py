"""Bounded ThreadPool scheduling for reserved recursive child batches."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from contextvars import Context, copy_context
from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True, slots=True)
class RecursiveCallReservation:
    """One already-reserved recursive child slot with its prompt."""

    prompt: str
    call_index: int
    child_depth: int


class RecursiveBatchError(RuntimeError):
    """Bounded all-or-nothing failure for one recursive batch."""

    def __init__(self) -> None:
        super().__init__("recursive child batch failed")


def run_reserved_batch(
    reservations: Sequence[RecursiveCallReservation],
    *,
    execute: Callable[[RecursiveCallReservation, Event], str],
    deadline_monotonic: float,
    max_parallel: int,
    on_retain_running: Callable[[set[Future[str]]], None],
) -> list[str]:
    """Run reserved child work with bounded fan-out and input-order results.

    Preserves atomic submit failure retention, first-failure cancellation of
    queued work, deadline-aware aggregation, and running-worker retention via
    ``on_retain_running`` (Futures are the retain tokens for still-running
    workers). Does not own recursive child construction or leases.
    """
    if not reservations:
        raise ValueError("reserved batch must not be empty")
    workers = min(max_parallel, len(reservations))
    answers: list[str] = []
    batch_cancelled = Event()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fleet-rlm-child")
    futures: list[Future[str]] = []
    try:
        try:
            for reservation in reservations:
                # Capture the submitter ContextVar state (MLflow turn span, etc.)
                # before the worker starts; copy_context() inside the worker would
                # see an empty thread-local context.
                ctx = copy_context()

                def _run(
                    reserved: RecursiveCallReservation = reservation,
                    context: Context = ctx,
                ) -> str:
                    return context.run(execute, reserved, batch_cancelled)

                futures.append(pool.submit(_run))
        except BaseException:
            batch_cancelled.set()
            pending = {future for future in futures if not future.done()}
            for future in pending:
                future.cancel()
            on_retain_running(pending)
            raise
        remaining = max(0.0, deadline_monotonic - time.monotonic())
        done, not_done = wait(futures, timeout=remaining, return_when=FIRST_EXCEPTION)
        failures = _future_failures(done)
        if failures or not_done:
            batch_cancelled.set()
            for future in not_done:
                future.cancel()
        if not_done:
            # Running Python threads cannot be force-cancelled. Each worker
            # retains its own lease until its deadline-bound LM call exits;
            # queued work is cancelled and executor teardown never performs
            # a second unbounded join on the Root worker.
            on_retain_running(not_done)
            if failures:
                raise RecursiveBatchError() from failures[0]
            raise TimeoutError("recursive child batch deadline exceeded")
        if failures:
            raise RecursiveBatchError() from failures[0]
        answers = [future.result(timeout=0) for future in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return answers


def _future_failures(futures: set[Future[str]]) -> list[BaseException]:
    failures: list[BaseException] = []
    for future in futures:
        if future.cancelled():
            continue
        try:
            failure = future.exception(timeout=0)
        except BaseException as exc:
            failures.append(exc)
        else:
            if failure is not None:
                failures.append(failure)
    return failures


__all__ = [
    "RecursiveBatchError",
    "RecursiveCallReservation",
    "run_reserved_batch",
]
