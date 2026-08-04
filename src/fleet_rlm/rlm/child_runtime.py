"""Provider-neutral child-runtime protocol consumed by the DSPy recursion path."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ChildRuntimeCleanupError(RuntimeError):
    """A child runtime could not be proved clean before Root commit."""


class ChildRuntimeAuthorizationError(RuntimeError):
    """A child runtime operation was attempted after Run authority was revoked."""


class ChildRuntimeLease(Protocol):
    """A dedicated child interpreter and its strictly owned cleanup operation."""

    interpreter: Any
    sandbox_id: str
    volume_id: str
    volume_subpath: str

    def close(self) -> None: """Close the child runtime lease and release its resources."""
...


ChildRuntimeFactory = Callable[[int], ChildRuntimeLease]


__all__ = [
    "ChildRuntimeAuthorizationError",
    "ChildRuntimeCleanupError",
    "ChildRuntimeFactory",
    "ChildRuntimeLease",
]
