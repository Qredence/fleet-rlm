"""Run-local authority revoked when the durable Turn Claim is lost."""


class RunAuthority:
    """Gate commit and Host-Mediated Tool effects for one Run."""

    __slots__ = ("_revoked",)

    def __init__(self) -> None:
        self._revoked = False

    @property
    def revoked(self) -> bool:
        return self._revoked

    def revoke(self) -> None:
        self._revoked = True
