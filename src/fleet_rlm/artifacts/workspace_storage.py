"""Artifact byte retrieval from mounted Workspace storage."""

from __future__ import annotations

from uuid import UUID

from fleet_rlm.workspace.storage import WorkspaceVolumeGateway


class WorkspaceArtifactBlobGateway:
    def __init__(self, gateway: WorkspaceVolumeGateway) -> None:
        self._gateway = gateway

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        return await self._gateway.read_bytes(workspace_id, logical_path)


__all__ = ["WorkspaceArtifactBlobGateway"]
