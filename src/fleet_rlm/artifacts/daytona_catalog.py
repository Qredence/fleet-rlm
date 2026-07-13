"""Production Artifact retrieval from database metadata and Workspace Volume bytes."""

from __future__ import annotations

from uuid import UUID

from fleet_rlm.artifacts.models import ArtifactAccess
from fleet_rlm.artifacts.reader import StoredArtifact
from fleet_rlm.daytona.workspace_volume import WorkspaceVolumeGateway
from fleet_rlm.persistence.repositories.artifacts import SqlAlchemyArtifactCatalog


class DaytonaArtifactCatalog:
    def __init__(
        self,
        repository: SqlAlchemyArtifactCatalog,
        gateway: WorkspaceVolumeGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact:
        stored = await self._repository.get(access=access, artifact_id=artifact_id)
        return StoredArtifact(ref=stored.ref, storage_ref=stored.storage_ref)


class DaytonaArtifactBlobGateway:
    def __init__(self, gateway: WorkspaceVolumeGateway) -> None:
        self._gateway = gateway

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        return await self._gateway.read_bytes(workspace_id, logical_path)


__all__ = ["DaytonaArtifactCatalog", "DaytonaArtifactBlobGateway"]
