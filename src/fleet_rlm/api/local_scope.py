"""Deterministic process-local scope for the single-user BYOK API."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

_LOCAL_USER_ID = uuid5(NAMESPACE_URL, "fleet-rlm/local-user")
_LOCAL_WORKSPACE_ID = uuid5(NAMESPACE_URL, "fleet-rlm/local-workspace")


@dataclass(frozen=True, slots=True)
class LocalScope:
    """Stable internal namespace keys for the local Fleet process."""

    user_id: UUID = _LOCAL_USER_ID
    workspace_id: UUID = _LOCAL_WORKSPACE_ID


async def get_local_scope() -> LocalScope:
    """Return the one local User and Workspace scope."""
    return LocalScope()
