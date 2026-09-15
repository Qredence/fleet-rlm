"""Application-owned Session catalog transitions and provider retirement."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.errors import SessionRetirementPendingError
from fleet_rlm.sessions.models import SessionRecord


class SessionRootRetirement(Protocol):
    """Provider-neutral root Session retirement boundary."""

    async def close_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        pass


class SessionActiveTurnDrain(Protocol):
    """Preparation-owned barrier for active Turns sharing a Session root."""

    async def wait_for_session_idle(
        self,
        workspace_id: UUID,
        session_id: UUID,
        *,
        deadline: float,
    ) -> None:
        pass


class NoOpSessionRetirement:
    """Deterministic retirement seam for credential-free compositions."""

    async def close_root_session(
        self,
        workspace_id: UUID | str,
        session_id: UUID | str,
        *,
        deadline: float | None = None,
    ) -> None:
        del workspace_id, session_id, deadline


class SessionLifecycle:
    """Own durable Session updates and the provider retirement that follows archive."""

    def __init__(
        self,
        catalog: SessionCatalog,
        retirement: SessionRootRetirement,
        *,
        active_turn_drain: SessionActiveTurnDrain | None = None,
    ) -> None:
        self._catalog = catalog
        self._retirement = retirement
        self._active_turn_drain = active_turn_drain

    async def update(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        title: str | None,
        status: str | None,
    ) -> SessionRecord:
        record = await self._catalog.update(
            session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            title=title,
            status=status,
        )
        if status == "archived":
            try:
                if self._active_turn_drain is not None:
                    await self._active_turn_drain.wait_for_session_idle(
                        workspace_id,
                        session_id,
                        deadline=asyncio.get_running_loop().time() + 30.0,
                    )
                await self._retirement.close_root_session(
                    workspace_id,
                    session_id,
                    deadline=asyncio.get_running_loop().time() + 30.0,
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raise SessionRetirementPendingError(session_id) from exc
        return record


__all__ = [
    "NoOpSessionRetirement",
    "SessionActiveTurnDrain",
    "SessionLifecycle",
    "SessionRootRetirement",
]
