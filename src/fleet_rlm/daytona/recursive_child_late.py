"""Ownership of late recursive-child acquisitions and quarantined cleanup."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Lock, Thread
from typing import Any

from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError

_FALLBACK_CLEANUP_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="fleet-late-child-cleanup-fallback",
)


class LateCleanupOwner:
    """Keep late provider work owned until its cleanup future settles."""

    def __init__(self, *, wait_timeout_s: float) -> None:
        """Initialize an owner for tracking late cleanup work.
        
        Parameters:
        	wait_timeout_s (float): Maximum time to wait for owned cleanup work to finish.
        """
        self._lock = Lock()
        self._pending: set[Future[Any]] = set()
        self._error: BaseException | None = None
        self._wait_timeout_s = wait_timeout_s

    def _record_error(self, exc: BaseException) -> None:
        """Record the first cleanup error observed by this owner."""
        with self._lock:
            if self._error is None:
                self._error = exc

    def _state(self) -> tuple[BaseException | None, bool]:
        """
        Collect completed work and report the first recorded error and whether work remains pending.
        
        Returns:
            tuple[BaseException | None, bool]: The first cleanup error, if any, and whether unfinished work remains.
        """
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
        """
        Completes a marker future with a result or an exception.
        
        Parameters:
        	marker (Future[None]): The future to complete.
        	error (BaseException | None): The exception to assign to the future, or `None` to complete it successfully.
        """
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
            """
            Record a completed future's failure and release it from pending ownership.
            
            Parameters:
                done (Future[Any]): The completed future whose terminal state is observed.
            """
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
        """Adopt a late lease for cleanup outside the event loop.
        
        Parameters:
            acquisition (Future[Any]): Future resolving to the lease to close.
            close_lease (Callable[[Any], None]): Function that closes the acquired lease.
        
        ChildRuntimeCleanupError and lease-closing failures are recorded for later reporting.
        """
        marker: Future[None] = Future()
        self.retain(marker)

        def close_late(done: Future[Any]) -> None:
            """Handle a completed late acquisition and arrange independent lease cleanup.
            
            Parameters:
            	done (Future[Any]): Completed future containing the acquired lease or an acquisition error.
            """
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
                """
                Close the acquired lease and mark the late cleanup operation complete.
                """
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
                try:
                    _FALLBACK_CLEANUP_EXECUTOR.submit(close)
                except BaseException as dispatch_error:
                    self._record_error(dispatch_error)
                    self._complete(marker, dispatch_error)

        acquisition.add_done_callback(close_late)

    def raise_if_failed(self) -> None:
        """Raise an observable late-cleanup error or pending-ownership error."""
        error, pending = self._state()
        if error is not None:
            raise ChildRuntimeCleanupError("recursive child cleanup failed") from error
        if pending:
            raise ChildRuntimeCleanupError("recursive child cleanup is still pending")

    def wait_owned(self) -> None:
        """
        Wait for retained cleanup work until the bounded ownership window expires.
        
        Raises:
            ChildRuntimeCleanupError: If cleanup fails or remains pending after the wait.
        """
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
