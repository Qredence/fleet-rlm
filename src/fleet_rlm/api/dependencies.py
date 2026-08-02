"""FastAPI dependency aliases for lifespan-composed Fleet RLM modules."""

from __future__ import annotations

import ipaddress
from typing import Annotated, Protocol

from fastapi import Depends, HTTPException, Request

from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactContent, ArtifactRef
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.chat.turn_lifecycle import TurnLifecycle
from fleet_rlm.config import Settings
from fleet_rlm.config_policy import ConfigPolicyService
from fleet_rlm.files.models import (
    AttachmentAccess,
    AttachmentRef,
    AttachmentRun,
    AttachmentUpload,
    PreparedAttachments,
    RunAttachmentSink,
)
from fleet_rlm.files.volume_storage import WorkspaceVolumeGateway
from fleet_rlm.files.workspace_access import WorkspaceFileService
from fleet_rlm.sessions.catalog import SessionCatalog


def require_loopback_client(request: Request) -> None:
    """Keep filesystem/compute administration local even on an unsafe API bind."""
    # Reject requests that carry proxy-forwarding headers: a local reverse proxy
    # connecting from 127.0.0.1 would make non-local clients appear loopback.
    # Reject by header presence (even empty values), not truthiness.
    forwarding_headers = ("x-forwarded-for", "forwarded", "x-real-ip")
    if any(header in request.headers for header in forwarding_headers):
        raise HTTPException(
            status_code=403,
            detail={"code": "settings_local_only", "message": "Available only from the local machine"},
        )
    host = request.client.host if request.client is not None else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail={"code": "settings_local_only", "message": "Available only from the local machine"},
        )


class AttachmentLifecyclePort(Protocol):
    async def upload(self, access: AttachmentAccess, upload: AttachmentUpload) -> AttachmentRef: ...

    async def metadata(self, access: AttachmentAccess, attachment_ids: tuple) -> tuple[AttachmentRef, ...]: ...

    async def prepare_run(
        self,
        access: AttachmentAccess,
        attachment_ids: tuple,
        run: AttachmentRun,
        sink: RunAttachmentSink,
    ) -> PreparedAttachments: ...


class ArtifactReaderPort(Protocol):
    async def metadata(self, access: ArtifactAccess, artifact_id) -> ArtifactRef: ...

    async def content(self, access: ArtifactAccess, artifact_id) -> ArtifactContent: ...


def get_turn_coordinator(request: Request) -> TurnCoordinator:
    if not getattr(request.app.state, "composition_ready", False):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    coordinator = getattr(request.app.state, "turn_coordinator", None)
    if coordinator is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return coordinator


def get_attachment_lifecycle(request: Request) -> AttachmentLifecyclePort:
    lifecycle = getattr(request.app.state, "attachment_lifecycle", None)
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return lifecycle


def get_artifact_reader(request: Request) -> ArtifactReaderPort:
    reader = getattr(request.app.state, "artifact_reader", None)
    if reader is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return reader


def get_session_catalog(request: Request) -> SessionCatalog:
    catalog = getattr(request.app.state, "session_catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="database not configured")
    return catalog


def get_turn_lifecycle(request: Request) -> TurnLifecycle:
    lifecycle = getattr(request.app.state, "turn_lifecycle", None)
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return lifecycle


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_config_policy(request: Request) -> ConfigPolicyService:
    policy = getattr(request.app.state, "config_policy", None)
    if not isinstance(policy, ConfigPolicyService):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return policy


def get_workspace_file_service(request: Request) -> WorkspaceFileService:
    service = getattr(request.app.state, "workspace_file_service", None)
    if not isinstance(service, WorkspaceFileService):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return service


def get_workspace_volume_gateway(request: Request) -> WorkspaceVolumeGateway:
    gateway = getattr(request.app.state, "workspace_volume_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return gateway


TurnCoordinatorDep = Annotated[TurnCoordinator, Depends(get_turn_coordinator)]
ArtifactReaderDep = Annotated[ArtifactReaderPort, Depends(get_artifact_reader)]
AttachmentLifecycleDep = Annotated[AttachmentLifecyclePort, Depends(get_attachment_lifecycle)]
SessionCatalogDep = Annotated[SessionCatalog, Depends(get_session_catalog)]
TurnLifecycleDep = Annotated[TurnLifecycle, Depends(get_turn_lifecycle)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ConfigPolicyDep = Annotated[ConfigPolicyService, Depends(get_config_policy)]
WorkspaceFileServiceDep = Annotated[WorkspaceFileService, Depends(get_workspace_file_service)]
WorkspaceVolumeGatewayDep = Annotated[WorkspaceVolumeGateway, Depends(get_workspace_volume_gateway)]

__all__ = [
    "ArtifactReaderDep",
    "AttachmentLifecycleDep",
    "ConfigPolicyDep",
    "SessionCatalogDep",
    "SettingsDep",
    "TurnCoordinatorDep",
    "TurnLifecycleDep",
    "WorkspaceFileServiceDep",
    "WorkspaceVolumeGatewayDep",
]
