"""Runtime-neutral mounted Workspace Volume storage contracts."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class VolumeFile:
    path: str
    modified_at: float


class VolumeBlobFs(Protocol):
    def write_bytes(self, logical_path: str, data: bytes) -> None: ...

    def read_bytes(self, logical_path: str, *, use_cache: bool = True) -> bytes: ...

    def exists(self, logical_path: str) -> bool: ...

    def remove(self, logical_path: str) -> None: ...


class VolumeTreeFs(VolumeBlobFs, Protocol):
    def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


class WorkspaceVolumeSession(Protocol):
    async def write_bytes(self, logical_path: str, data: bytes) -> None: ...

    async def read_bytes(self, logical_path: str) -> bytes: ...

    async def remove_bytes(self, logical_path: str) -> None: ...

    async def list_files(
        self,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


class WorkspaceVolumeGateway(Protocol):
    def open_workspace(self, workspace_id: UUID) -> AbstractAsyncContextManager[WorkspaceVolumeSession]: ...

    async def write_bytes(self, workspace_id: UUID, logical_path: str, data: bytes) -> None: ...

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes: ...

    async def remove_bytes(self, workspace_id: UUID, logical_path: str) -> None: ...

    async def list_files(
        self,
        workspace_id: UUID,
        logical_root: str,
        *,
        max_depth: int,
        max_files: int,
    ) -> tuple[VolumeFile, ...]: ...


__all__ = [
    "VolumeBlobFs",
    "VolumeFile",
    "VolumeTreeFs",
    "WorkspaceVolumeGateway",
    "WorkspaceVolumeSession",
]
