"""Authorized, integrity-checked reads of committed Artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fleet_rlm.artifacts.errors import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactStorageError,
)
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactContent, ArtifactRef


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Private catalog value containing an opaque committed byte reference."""

    ref: ArtifactRef
    storage_ref: str


class ArtifactCatalog(Protocol):
    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact: ...


class ArtifactBlobGateway(Protocol):
    async def read(self, workspace_id: UUID, logical_path: str) -> bytes: ...


class ArtifactReader:
    """Read committed metadata and content without exposing storage topology."""

    def __init__(self, *, catalog: ArtifactCatalog, blobs: ArtifactBlobGateway) -> None:
        self._catalog = catalog
        self._blobs = blobs

    async def _stored(self, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact:
        try:
            return await self._catalog.get(access=access, artifact_id=artifact_id)
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactStorageError("Artifact storage is unavailable") from exc

    async def metadata(self, access: ArtifactAccess, artifact_id: UUID) -> ArtifactRef:
        return (await self._stored(access, artifact_id)).ref

    async def content(self, access: ArtifactAccess, artifact_id: UUID) -> ArtifactContent:
        stored = await self._stored(access, artifact_id)
        try:
            data = await self._blobs.read(access.workspace_id, stored.storage_ref)
        except ArtifactError:
            raise
        except (FileNotFoundError, KeyError) as exc:
            raise ArtifactNotFoundError("Artifact not found") from exc
        except Exception as exc:
            raise ArtifactStorageError("Artifact storage is unavailable") from exc
        if len(data) != stored.ref.byte_size:
            raise ArtifactNotFoundError("Artifact not found")
        return ArtifactContent(metadata=stored.ref, data=data)
