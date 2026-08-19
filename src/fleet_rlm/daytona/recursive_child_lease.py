"""Explicit close state for a native recursive child runtime lease."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Condition, get_ident
from typing import Any


class ChildRuntimeLeaseState(StrEnum):
    """States observed by callers of a child runtime lease."""

    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


@dataclass(slots=True)
class ChildRuntimeLease:
    """One synchronously usable child interpreter and its owned cleanup action.

    ``FAILED`` is an explicit terminal observation for the close attempt. A
    later caller re-observes the same failure rather than starting a second
    provider cleanup, while callers that arrive during ``CLOSING`` join the
    one in-flight close operation.
    """

    interpreter: Any
    sandbox_id: str
    volume_id: str
    volume_subpath: str
    _close: Callable[[], None] = field(repr=False)
    _state: ChildRuntimeLeaseState = field(default=ChildRuntimeLeaseState.OPEN, init=False, repr=False)
    _close_error: BaseException | None = field(default=None, init=False, repr=False)
    _condition: Condition = field(default_factory=Condition, init=False, repr=False)
    _closing_thread_id: int | None = field(default=None, init=False, repr=False)

    @property
    def state(self) -> ChildRuntimeLeaseState:
        """
        Expose the lease's current lifecycle state.
        
        Returns:
        	ChildRuntimeLeaseState: The current lease state.
        """
        with self._condition:
            return self._state

    @property
    def close_error(self) -> BaseException | None:
        """Return the terminal close error, if the lease is ``FAILED``."""
        with self._condition:
            return self._close_error

    def close(self) -> None:
        """Close the child runtime lease exactly once.
        
        Concurrent callers wait for an in-progress close and observe its result. Cleanup
        failures are retained and re-raised by subsequent callers.
        
        Raises:
            RuntimeError: If cleanup is invoked recursively by the closing thread.
            BaseException: The exception raised by the cleanup callback.
        """
        with self._condition:
            if self._state is ChildRuntimeLeaseState.CLOSED:
                return
            if self._state is ChildRuntimeLeaseState.CLOSING:
                if self._closing_thread_id == get_ident():
                    raise RuntimeError("recursive child lease close is not reentrant")
                while self._state is ChildRuntimeLeaseState.CLOSING:
                    self._condition.wait()
                if self._state is ChildRuntimeLeaseState.CLOSED:
                    return
                if self._state is ChildRuntimeLeaseState.FAILED:
                    error = self._close_error
                    if error is None:
                        raise RuntimeError("recursive child lease close failed")
                    raise error
            if self._state is ChildRuntimeLeaseState.FAILED:
                error = self._close_error
                if error is None:
                    raise RuntimeError("recursive child lease close failed")
                raise error
            self._state = ChildRuntimeLeaseState.CLOSING
            self._closing_thread_id = get_ident()

        try:
            self._close()
        except BaseException as exc:
            with self._condition:
                self._close_error = exc
                self._state = ChildRuntimeLeaseState.FAILED
                self._closing_thread_id = None
                self._condition.notify_all()
            raise
        else:
            with self._condition:
                self._state = ChildRuntimeLeaseState.CLOSED
                self._closing_thread_id = None
                self._condition.notify_all()


__all__ = ["ChildRuntimeLease", "ChildRuntimeLeaseState"]
