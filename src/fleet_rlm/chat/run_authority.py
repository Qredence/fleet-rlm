"""Run-local authority revoked when the durable Run Claim is lost."""

from __future__ import annotations

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
