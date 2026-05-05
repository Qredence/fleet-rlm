"""Sandbox service encapsulating Daytona sandbox operations with error mapping."""

from __future__ import annotations

from daytona import (
    DaytonaAuthenticationError,
    DaytonaAuthorizationError,
    DaytonaConnectionError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
)
from fastapi import HTTPException

from fleet_rlm.utils.sandbox_ownership import sandbox_owner_labels

from ..schemas.sandbox import (
    SandboxArchiveResponse,
    SandboxDetailResponse,
    SandboxListResponse,
)
from . import sandboxes as _sandboxes

_DAYTONA_NOT_FOUND_ERRORS: tuple[type[BaseException], ...] = (DaytonaNotFoundError,)
_DAYTONA_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    DaytonaConnectionError,
    DaytonaAuthenticationError,
    DaytonaAuthorizationError,
    DaytonaTimeoutError,
)


def _map_daytona_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, _DAYTONA_NOT_FOUND_ERRORS):
        return HTTPException(
            status_code=404,
            detail=f"Sandbox not found: {exc}",
        )
    if isinstance(exc, _DAYTONA_UNAVAILABLE_ERRORS):
        return HTTPException(
            status_code=503,
            detail=f"Sandbox service unavailable: {exc}",
        )
    raise exc


class SandboxService:
    """Encapsulates Daytona sandbox operations with error mapping."""

    def __init__(self) -> None:
        pass

    async def list_sandboxes(
        self,
        *,
        page: int,
        limit: int,
        tenant_claim: str,
        user_claim: str,
        allow_unlabeled_legacy: bool,
    ) -> SandboxListResponse:
        """Return a paginated list of active Daytona sandboxes."""
        try:
            return await _sandboxes.load_sandbox_list(
                page=page,
                limit=limit,
                owner_labels=sandbox_owner_labels(
                    tenant_claim=tenant_claim,
                    user_claim=user_claim,
                ),
                allow_unlabeled_legacy=allow_unlabeled_legacy,
            )
        except _DAYTONA_NOT_FOUND_ERRORS as exc:
            raise _map_daytona_error(exc) from exc
        except _DAYTONA_UNAVAILABLE_ERRORS as exc:
            raise _map_daytona_error(exc) from exc

    async def get_sandbox_detail(
        self,
        *,
        sandbox_id: str,
        tenant_claim: str,
        user_claim: str,
        allow_unlabeled_legacy: bool,
    ) -> SandboxDetailResponse:
        """Return detailed information for a single Daytona sandbox."""
        try:
            return await _sandboxes.load_sandbox_detail(
                sandbox_id=sandbox_id,
                owner_labels=sandbox_owner_labels(
                    tenant_claim=tenant_claim,
                    user_claim=user_claim,
                ),
                allow_unlabeled_legacy=allow_unlabeled_legacy,
            )
        except _DAYTONA_NOT_FOUND_ERRORS as exc:
            raise _map_daytona_error(exc) from exc
        except _DAYTONA_UNAVAILABLE_ERRORS as exc:
            raise _map_daytona_error(exc) from exc

    async def delete_sandbox(
        self,
        *,
        sandbox_id: str,
        tenant_claim: str,
        user_claim: str,
        allow_unlabeled_legacy: bool,
    ) -> None:
        """Stop and delete a Daytona sandbox."""
        try:
            await _sandboxes.delete_sandbox(
                sandbox_id=sandbox_id,
                owner_labels=sandbox_owner_labels(
                    tenant_claim=tenant_claim,
                    user_claim=user_claim,
                ),
                allow_unlabeled_legacy=allow_unlabeled_legacy,
            )
        except _DAYTONA_NOT_FOUND_ERRORS as exc:
            raise _map_daytona_error(exc) from exc
        except _DAYTONA_UNAVAILABLE_ERRORS as exc:
            raise _map_daytona_error(exc) from exc

    async def archive_sandbox(
        self,
        *,
        sandbox_id: str,
        tenant_claim: str,
        user_claim: str,
        allow_unlabeled_legacy: bool,
    ) -> SandboxArchiveResponse:
        """Archive a Daytona sandbox to cold storage."""
        try:
            await _sandboxes.archive_sandbox(
                sandbox_id=sandbox_id,
                owner_labels=sandbox_owner_labels(
                    tenant_claim=tenant_claim,
                    user_claim=user_claim,
                ),
                allow_unlabeled_legacy=allow_unlabeled_legacy,
            )
        except _DAYTONA_NOT_FOUND_ERRORS as exc:
            raise _map_daytona_error(exc) from exc
        except _DAYTONA_UNAVAILABLE_ERRORS as exc:
            raise _map_daytona_error(exc) from exc
        return SandboxArchiveResponse()
