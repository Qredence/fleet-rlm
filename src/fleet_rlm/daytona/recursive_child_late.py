"""Ownership of late recursive-child acquisitions and quarantined cleanup."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, wait
from threading import Lock, Thread
from typing import Any

from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError


class LateCleanupOwner:
    """Keep late provider work owned until its cleanup future settles."""

    def __init__(self, *, wait_timeout_s: float) -> None:
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
        with self._lock:
            for future in tuple(self._pending):
                if not future.done():
                    continue
                try:
                    error = future.exception()
                except BaseException as exc:
                    error = exc
                if error is not None and self._error is None:
                    self._error = error
                self._pending.discard(future)
            return self._error, any(not future.done() for future in self._pending)

    @staticmethod
    def _complete(marker: Future[None], error: BaseException | None = None) -> None:
        if marker.done():
            return
        if error is None:
            marker.set_result(None)
        else:
            marker.set_exception(error)

    def retain(self, future: Future[Any]) -> None:
        """Retain one future and observe its terminal exception."""
        with self._lock:
            self._pending.add(future)

        def settled(done: Future[Any]) -> None:
            try:
                error = done.exception()
            except BaseException as exc:
                self._record_error(exc)
            else:
                if error is not None:
                    self._record_error(error)
            with self._lock:
                self._pending.discard(done)

        future.add_done_callback(settled)

    def adopt_late_acquisition(
        self,
        acquisition: Future[Any],
        close_lease: Callable[[Any], None],
    ) -> None:
        """Adopt a late lease and close it from a thread independent of the loop."""
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
            try:
                lease = done.result()
            except ChildRuntimeCleanupError as exc:
                self._record_error(exc)
                self._complete(marker)
                return
            except BaseException:
                self._complete(marker)
                return

            def close() -> None:
                try:
                    close_lease(lease)
                except BaseException as exc:
                    self._record_error(exc)
                finally:
                    self._complete(marker)

            thread = Thread(target=close, name="fleet-late-child-cleanup", daemon=True)
            try:
                thread.start()
            except BaseException as exc:
                self._record_error(exc)
                # A thread-start failure has no safe asynchronous owner left;
                # make one best-effort synchronous close before surfacing it.
                close()

        acquisition.add_done_callback(close_late)

    def raise_if_failed(self) -> None:
        """Raise an observable late-cleanup error or pending-ownership error."""
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def wait_owned(self) -> None:
        """Wait for retained work within the bounded quarantine window."""
        wait_deadline = time.monotonic() + max(self._wait_timeout_s, 1.0)
        while True:
            with self._lock:
                pending = tuple(future for future in self._pending if not future.done())
            if not pending:
                break
            remaining = max(0.0, wait_deadline - time.monotonic())
            _, still_pending = wait(pending, timeout=remaining)
            if still_pending:
                self._record_error(TimeoutError("recursive child cleanup quarantine timed out"))
                break
        self.raise_if_failed()


__all__ = ["LateCleanupOwner"]
