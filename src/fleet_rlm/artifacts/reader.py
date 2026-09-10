"""Authorized, integrity-checked reads of committed Artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fleet_rlm.artifacts.errors import (
    ArtifactError,
    ArtifactNotFoundError,
    ArtifactStorageError,
    ArtifactValidationError,
)
from fleet_rlm.artifacts.models import KIND_MEDIA_TYPES, ArtifactAccess, ArtifactContent, ArtifactRef


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Private catalog value containing an opaque committed byte reference."""

    ref: ArtifactRef
    storage_ref: str


class ArtifactCatalog(Protocol):
    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact: ...


class ArtifactBlobGateway(Protocol):
    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes: ...


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

    async def content(
        self, access: ArtifactAccess, artifact_id: UUID, *, max_bytes: int | None = None
    ) -> ArtifactContent:
        """Read verified bytes, optionally rejecting oversized content before fetch."""
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ArtifactValidationError("Artifact byte allowance must be a positive integer")
        stored = await self._stored(access, artifact_id)
        if stored.ref.media_type != KIND_MEDIA_TYPES.get(stored.ref.kind):
            raise ArtifactValidationError("Artifact content is not a supported text type")
        if max_bytes is not None and stored.ref.byte_size > max_bytes:
            raise ArtifactValidationError("Artifact exceeds the selected input byte allowance")
        try:
            data = await self._blobs.read_bytes(access.workspace_id, stored.storage_ref)
        except ArtifactError:
            raise
        except (FileNotFoundError, KeyError) as exc:
            raise ArtifactNotFoundError("Artifact not found") from exc
        except Exception as exc:
            raise ArtifactStorageError("Artifact storage is unavailable") from exc
        if len(data) != stored.ref.byte_size or hashlib.sha256(data).hexdigest() != stored.ref.checksum_sha256:
            raise ArtifactNotFoundError("Artifact not found")
        return ArtifactContent(metadata=stored.ref, data=data)
