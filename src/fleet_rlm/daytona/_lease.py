"""Private lifecycle primitives for Daytona-owned leases.

The public runtime exposes the small ``RootSessionLease`` handle.  Provider
modules keep their SDK and cleanup details behind this module so a close has
one explicit, cancellation-safe state machine.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from fleet_rlm.daytona._cleanup import await_cleanup


class LeaseState(StrEnum):
    """Lifecycle states shared by public root and child handles."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class RootSessionLease:
    """Cancellation-safe owner for one reusable root provider lease.

    ``RootSessionLease`` intentionally accepts the historical positional shape
    ``(key, lease, release_callback, on_closed)``.  The existing provider
    environment uses that shape while the public ``DaytonaRuntime`` supplies
    richer keyword metadata.  Keeping the primitive here lets the public
    facade deepen ownership without making provider callers understand the
    admission, binding, or deletion machinery.
    """

    def __init__(
        self,
        key: Any,
        lease: Any,
        release_callback: Callable[[Any], Awaitable[Any] | Any],
        on_closed: Callable[[RootSessionLease], Awaitable[Any] | Any] | None = None,
        *,
        spec: Any | None = None,
        sandbox: Any | None = None,
        interpreter: Any | None = None,
        broker: Any | None = None,
        volume: Any | None = None,
        volume_id: str | None = None,
        mount_path: str | None = None,
        volume_subpath: str | None = None,
    ) -> None:
        self.key = key
        self.spec = spec
        self.lease = lease
        self.release_callback = release_callback
        self.on_closed = on_closed
        self.sandbox = sandbox if sandbox is not None else getattr(lease, "sandbox", None)
        self.interpreter = interpreter if interpreter is not None else getattr(lease, "interpreter", None)
        self.broker = (
            broker
            if broker is not None
            else getattr(self.interpreter, "broker", getattr(self.interpreter, "_http_broker", None))
        )
        self.volume = volume if volume is not None else getattr(lease, "volume", None)
        sandbox_id = getattr(lease, "sandbox_id", None) or getattr(self.sandbox, "id", None)
        self.sandbox_id = str(sandbox_id or "")
        self.volume_id = volume_id or _optional_text(getattr(lease, "volume_id", None))
        self.mount_path = mount_path or _optional_text(getattr(lease, "mount_path", None))
        self.volume_subpath = volume_subpath or _optional_text(getattr(lease, "volume_subpath", None))
        self._state = LeaseState.OPEN
        # Optional composition owner marker used when this primitive is shared
        # by a provider facade and its preparation adapter.
        self._environment_provider_owner: Any | None = None
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._close_error: BaseException | None = None
        self._notify_on_close = False

    @property
    def state(self) -> LeaseState:
        """Return the explicit lifecycle state of this lease."""
        return self._state

    @property
    def status(self) -> LeaseState:
        """Alias for ``state`` for status-oriented lifecycle consumers."""
        return self.state

    @property
    def closed(self) -> bool:
        """Whether cleanup completed successfully."""
        return self._state is LeaseState.CLOSED

    @property
    def closing(self) -> bool:
        """Whether one cleanup task currently owns provider release."""
        return self._state is LeaseState.CLOSING

    @property
    def failed(self) -> bool:
        """Whether the last cleanup attempt failed and remains retryable."""
        return self._state is LeaseState.FAILED

    @property
    def close_error(self) -> BaseException | None:
        """Return the last cleanup error without exposing provider text."""
        return self._close_error

    async def close(self, *, notify: bool = True, deadline: float | None = None) -> None:
        """Release the provider lease exactly once, joining concurrent closes."""
        async with self._close_lock:
            if self._state is LeaseState.CLOSED:
                return
            self._notify_on_close = self._notify_on_close or notify
            task = self._close_task
            if task is None:
                self._state = LeaseState.CLOSING
                task = asyncio.create_task(self._perform_close(), name="fleet-daytona-root-lease-close")
                self._close_task = task
        if deadline is None:
            await asyncio.shield(task)
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("root Session lease close timed out")
        await asyncio.wait_for(asyncio.shield(task), timeout=remaining)

    async def _perform_close(self) -> None:
        current = asyncio.current_task()
        try:
            result = await await_cleanup(self.release_callback, self.lease)
            if _cleanup_failed(self.lease) or _cleanup_failed(result):
                raise RuntimeError("root Session cleanup failed")
        except BaseException as exc:
            async with self._close_lock:
                if self._close_task is current:
                    self._state = LeaseState.FAILED
                    self._close_error = exc
                    self._close_task = None
            raise

        async with self._close_lock:
            if self._close_task is not current:
                return
            self._state = LeaseState.CLOSED
            self._close_error = None
            self._close_task = None
            notify = self._notify_on_close
            self._notify_on_close = False
        if notify and self.on_closed is not None:
            # A map-removal observer must never turn a successful provider
            # release into a failed close.  The owner can still discover the
            # closed handle through its identity-safe registry cleanup.
            with contextlib.suppress(BaseException):
                await await_cleanup(self.on_closed, self)

    async def release(self) -> None:
        """Compatibility alias used by lifecycle cleanup callbacks."""
        await self.close()


def _cleanup_failed(value: Any) -> bool:
    """Recognize typed failed-cleanup results without exposing provider data."""
    if value is None:
        return False
    if value is False:
        return True
    if bool(getattr(value, "failed", False)):
        return True
    if getattr(value, "first_error", None) is not None:
        return True
    quarantine = getattr(value, "quarantine", None)
    return bool(getattr(quarantine, "quarantined", False))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = ["LeaseState", "RootSessionLease"]
