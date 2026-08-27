"""Run-local authority revoked when the durable Run Claim is lost."""

from __future__ import annotations

import contextlib
from collections.abc import Callable


class RunAuthority:
    """Gate commit and Host-Mediated Tool effects for one Run."""

    __slots__ = ("_listeners", "_revoked")

    def __init__(self) -> None:
        self._revoked = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def revoked(self) -> bool:
        return self._revoked

    def is_live(self) -> bool:
        """Return whether this Run may still perform host-mediated effects."""
        return not self._revoked

    def add_revoke_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback that fences suspended operations on revocation."""
        if self._revoked:
            listener()
            return lambda: None
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def revoke(self) -> None:
        if self._revoked:
            return
        self._revoked = True
        listeners = tuple(self._listeners)
        self._listeners.clear()
        for listener in listeners:
            try:
                listener()
            except BaseException:
                # Revocation must not be blocked by an observer defect.
                continue
