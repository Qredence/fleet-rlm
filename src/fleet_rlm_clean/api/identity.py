"""Synthetic/dev identity for kernel phase (H-001 replaces with Neon JWT)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import Header


# Stable defaults so local runs are deterministic without auth.
_DEFAULT_USER = uuid5(NAMESPACE_URL, "fleet-rlm-clean/dev-user")
_DEFAULT_WORKSPACE = uuid5(NAMESPACE_URL, "fleet-rlm-clean/dev-workspace")


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    user_id: UUID
    workspace_id: UUID


def get_request_identity(
    x_fleet_user_id: UUID | None = Header(default=None, alias="X-Fleet-User-Id"),
    x_fleet_workspace_id: UUID | None = Header(default=None, alias="X-Fleet-Workspace-Id"),
) -> RequestIdentity:
    """Resolve synthetic identity from optional headers, else stable dev defaults."""
    return RequestIdentity(
        user_id=x_fleet_user_id or _DEFAULT_USER,
        workspace_id=x_fleet_workspace_id or _DEFAULT_WORKSPACE,
    )
