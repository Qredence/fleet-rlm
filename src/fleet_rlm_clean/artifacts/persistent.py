"""Production Artifact retrieval from database metadata and Workspace Volume bytes."""

from __future__ import annotations

from uuid import UUID

from fleet_rlm_clean.artifacts.models import ArtifactRef
from fleet_rlm_clean.daytona.workspace_volume import WorkspaceVolumeGateway
from fleet_rlm_clean.persistence.repositories.files import SqlAlchemyArtifactRepository


class VolumeArtifactStore:
    def __init__(
        self,
        repository: SqlAlchemyArtifactRepository,
        gateway: WorkspaceVolumeGateway,
    ) -> None:
        self._repository = repository
        self._gateway = gateway

    async def get(self, artifact_id: UUID, *, user_id: UUID, workspace_id: UUID) -> ArtifactRef:
        return (await self._repository.get(artifact_id, user_id=user_id, workspace_id=workspace_id)).ref

    async def read_bytes(self, artifact_id: UUID, *, user_id: UUID, workspace_id: UUID) -> bytes:
        stored = await self._repository.get(artifact_id, user_id=user_id, workspace_id=workspace_id)
        return await self._gateway.read_bytes(workspace_id, stored.storage_ref)


__all__ = ["VolumeArtifactStore"]
