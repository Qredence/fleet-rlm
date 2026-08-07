"""Provider-neutral Sandbox binding records and store ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fleet_rlm.files.volume_paths import DEFAULT_VOLUME_MOUNT_PATH

_ZERO_UUID = UUID(int=0)


@dataclass(frozen=True, slots=True)
class SandboxBinding:
    session_id: UUID
    sandbox_id: str | None
    workspace_id: UUID
    volume_id: str | None
    volume_subpath: str
    mount_path: str = DEFAULT_VOLUME_MOUNT_PATH
    provider_state: str = "missing"
    last_verified_at: datetime | None = None


class SandboxBindingStore(Protocol):
    """Persist per-session provider Sandbox/Volume binding metadata."""

    async def get(self, session_id: UUID) -> SandboxBinding | None: ...

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...


def require_non_zero_workspace_id(workspace_id: UUID) -> UUID:
    if not isinstance(workspace_id, UUID):
        raise TypeError("workspace_id must be a UUID")
    if workspace_id == _ZERO_UUID:
        raise ValueError("workspace_id must not be the zero UUID")
    return workspace_id


def workspace_volume_subpath(workspace_id: UUID) -> str:
    """Return the canonical provider-neutral volume subpath for one workspace."""

    return f"workspaces/{require_non_zero_workspace_id(workspace_id)}"


def require_scoped_volume_subpath(subpath: str, *, workspace_id: UUID | None = None) -> str:
    """Validate and normalize a workspace-scoped provider volume subpath."""

    if not isinstance(subpath, str) or not subpath.strip():
        raise ValueError("VolumeMount without workspace subpath is rejected")
    normalized = subpath.strip().strip("/")
    if ".." in normalized.split("/"):
        raise ValueError("volume subpath must not contain path traversal")
    if not normalized.startswith("workspaces/"):
        raise ValueError("volume subpath must be under workspaces/<workspace_id>")
    rest = normalized.removeprefix("workspaces/")
    if not rest or "/" in rest:
        raise ValueError("volume subpath must be exactly workspaces/<workspace_id>")
    if workspace_id is not None and normalized != workspace_volume_subpath(workspace_id):
        raise ValueError("volume subpath does not match workspace_id")
    return normalized


def validate_sandbox_binding(binding: SandboxBinding) -> SandboxBinding:
    """Validate binding identity and workspace-scoped volume ownership."""

    require_non_zero_workspace_id(binding.workspace_id)
    require_scoped_volume_subpath(binding.volume_subpath, workspace_id=binding.workspace_id)
    return binding


class InMemorySandboxBindingStore:
    """Test/local binding store that does not require SQL."""

    def __init__(self) -> None:
        self._items: dict[UUID, SandboxBinding] = {}

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        return self._items.get(session_id)

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        self._items[binding.session_id] = binding
        return binding


__all__ = [
    "InMemorySandboxBindingStore",
    "SandboxBinding",
    "SandboxBindingStore",
    "require_non_zero_workspace_id",
    "require_scoped_volume_subpath",
    "validate_sandbox_binding",
    "workspace_volume_subpath",
]
