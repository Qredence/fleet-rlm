"""Provider-neutral child-runtime protocol consumed by the DSPy recursion path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fleet_rlm.rlm.dspy_interpreter_contract import CodeInterpreter


class ChildRuntimeCleanupError(RuntimeError):
    """A child runtime could not be proved clean before Root commit."""


class ChildRuntimeAuthorizationError(RuntimeError):
    """A child runtime operation was attempted after Run authority was revoked."""


class ChildRuntimeLease(Protocol):
    """A dedicated child interpreter and its strictly owned cleanup operation."""

    @property
    def interpreter(self) -> CodeInterpreter:
        """Return the caller-owned interpreter for this child lease."""
        ...

    sandbox_id: str
    volume_id: str
    volume_subpath: str

    def close(self) -> None:
        """Close the child runtime lease and release its resources."""
        ...


class ChildRuntimeFactory(Protocol):
    """Acquire child leases and own late acquisitions that never produced a lease.

    ``wait_owned`` and ``raise_if_cleanup_failed`` sit on the factory, not the
    lease: a timed-out Daytona acquisition may still complete with a Sandbox
    that must be closed even though no ``ChildRuntimeLease`` was returned.
    """

    def __call__(self, call_index: int) -> ChildRuntimeLease:
        """Return a dedicated child runtime lease for ``call_index``."""
        ...

    def wait_owned(self) -> None:
        """Block until late factory-owned acquisitions have closed."""
        ...

    def raise_if_cleanup_failed(self) -> None:
        """Raise when factory-owned late cleanup failed or is still pending."""
        ...


__all__ = [
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
]
