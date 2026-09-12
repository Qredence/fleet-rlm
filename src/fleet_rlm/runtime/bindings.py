"""Provider-neutral Sandbox binding records and store ports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock
from typing import Protocol
from uuid import UUID

from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH

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
    # Monotonic provider-binding generation.  Recovery and cleanup operations
    # must never overwrite a newer replacement with stale state.
    generation: int = 1


class SandboxBindingStore(Protocol):
    """Persist per-session provider Sandbox/Volume binding metadata."""

    async def get(self, session_id: UUID) -> SandboxBinding | None: ...

    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None: ...

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding: ...

    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        raise NotImplementedError


class BindingGenerationAuthority:
    """Thread-safe in-process view of the durable binding fence.

    Native callbacks are synchronous, so they cannot await a database lookup.
    Session-manager reads and writes observe the durable row here; a
    replacement therefore revokes every older lease before its next callback.
    Unknown identities are rejected when this authority is used, while legacy
    test doubles that do not install an authority retain their existing seam.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[tuple[UUID, UUID], tuple[int, str | None, str]] = {}

    def observe(self, binding: SandboxBinding) -> None:
        key = (binding.session_id, binding.workspace_id)
        candidate = (binding.generation, binding.sandbox_id, binding.provider_state)
        with self._lock:
            current = self._items.get(key)
            if current is None or binding.generation > current[0]:
                self._items[key] = candidate
                return
            if binding.generation != current[0] or binding.sandbox_id != current[1]:
                return
            # A provider fence is terminal for this generation. A delayed
            # read from before the fence may still report ``running``; never
            # let that stale observation re-arm an old native backend. A
            # legitimate restart is published under a new generation by the
            # session manager, so this does not block valid transitions.
            if current[2] != "running" and binding.provider_state == "running":
                return
            self._items[key] = candidate

    def is_current(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> bool:
        with self._lock:
            current = self._items.get((session_id, workspace_id))
        if current is None:
            return False
        return current == (generation, sandbox_id, "running")

    def revoke(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        sandbox_id: str,
        generation: int,
    ) -> None:
        """Mark one exact generation unusable without touching newer state."""
        key = (session_id, workspace_id)
        with self._lock:
            current = self._items.get(key)
            if current is not None and current[:2] == (generation, sandbox_id):
                self._items[key] = (generation, sandbox_id, "revoked")


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
    if type(binding.generation) is not int or binding.generation < 1:
        raise ValueError("sandbox binding generation must be a positive integer")
    return binding


class InMemorySandboxBindingStore:
    """Test/local binding store that does not require SQL."""

    def __init__(self) -> None:
        self._items: dict[UUID, SandboxBinding] = {}

    async def get(self, session_id: UUID) -> SandboxBinding | None:
        return self._items.get(session_id)

    async def get_scoped(self, session_id: UUID, *, workspace_id: UUID) -> SandboxBinding | None:
        binding = self._items.get(session_id)
        if binding is None or binding.workspace_id != workspace_id:
            return None
        return binding

    async def upsert(self, binding: SandboxBinding) -> SandboxBinding:
        validate_sandbox_binding(binding)
        existing = self._items.get(binding.session_id)
        if existing is not None and existing.workspace_id != binding.workspace_id:
            raise ValueError("sandbox binding workspace scope mismatch")
        if existing is not None and binding.generation < existing.generation:
            raise ValueError("stale sandbox binding generation")
        if (
            existing is not None
            and binding.generation == existing.generation
            and binding.sandbox_id != existing.sandbox_id
        ):
            raise ValueError("conflicting sandbox binding identity for generation")
        if (
            existing is not None
            and binding.generation == existing.generation
            and existing.sandbox_id == binding.sandbox_id
            and existing.provider_state != "running"
            and binding.provider_state == "running"
        ):
            raise ValueError("stale running sandbox binding generation")
        self._items[binding.session_id] = binding
        return binding

    async def replace_with_next_generation(self, binding: SandboxBinding) -> SandboxBinding:
        """Atomically allocate the next identity generation for local state."""
        validate_sandbox_binding(binding)
        existing = self._items.get(binding.session_id)
        if existing is not None and existing.workspace_id != binding.workspace_id:
            raise ValueError("sandbox binding workspace scope mismatch")
        if existing is None:
            generation = binding.generation
        elif existing.sandbox_id == binding.sandbox_id and existing.provider_state == "running":
            generation = existing.generation
        else:
            generation = existing.generation + 1
        replacement = replace(binding, generation=generation)
        self._items[binding.session_id] = replacement
        return replacement


__all__ = [
    "BindingGenerationAuthority",
    "InMemorySandboxBindingStore",
    "SandboxBinding",
    "SandboxBindingStore",
    "require_non_zero_workspace_id",
    "require_scoped_volume_subpath",
    "validate_sandbox_binding",
    "workspace_volume_subpath",
]
