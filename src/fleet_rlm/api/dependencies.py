"""FastAPI dependency aliases for lifespan-composed Fleet RLM modules."""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.chat.turn_lifecycle import TurnLifecycle
from fleet_rlm.composition.inventory import RuntimeInventory, get_runtime_inventory
from fleet_rlm.config import Settings
from fleet_rlm.config_policy import ConfigPolicyService
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.volume_storage import WorkspaceVolumeGateway
from fleet_rlm.files.workspace_access import WorkspaceFileService
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.skills.catalog import SkillCatalog


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


def get_ready_runtime_inventory(request: Request) -> RuntimeInventory:
    if not getattr(request.app.state, "composition_ready", False):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    inventory = get_runtime_inventory(request.app)
    if inventory is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return inventory


def get_turn_coordinator(request: Request) -> TurnCoordinator:
    inventory = get_ready_runtime_inventory(request)
    coordinator = inventory.turn_coordinator
    if coordinator is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return coordinator


def get_attachment_lifecycle(request: Request) -> AttachmentLifecycle:
    lifecycle = get_ready_runtime_inventory(request).attachment_lifecycle
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return lifecycle


def get_artifact_reader(request: Request) -> ArtifactReader:
    reader = get_ready_runtime_inventory(request).artifact_reader
    if reader is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return reader


def get_session_catalog(request: Request) -> SessionCatalog:
    catalog = get_ready_runtime_inventory(request).session_catalog
    if catalog is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return catalog


def get_turn_lifecycle(request: Request) -> TurnLifecycle:
    lifecycle = get_ready_runtime_inventory(request).turn_lifecycle
    if lifecycle is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return lifecycle


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_skill_catalog(request: Request) -> SkillCatalog:
    catalog = getattr(request.app.state, "skill_catalog", None)
    if not isinstance(catalog, SkillCatalog):
        raise RuntimeError("bundled Skill catalog is unavailable")
    return catalog


def get_config_policy(request: Request) -> ConfigPolicyService:
    policy = get_ready_runtime_inventory(request).config_policy
    if not isinstance(policy, ConfigPolicyService):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return policy


def get_workspace_file_service(request: Request) -> WorkspaceFileService:
    service = get_ready_runtime_inventory(request).workspace_file_service
    if not isinstance(service, WorkspaceFileService):
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return service


def get_workspace_volume_gateway(request: Request) -> WorkspaceVolumeGateway:
    gateway = get_ready_runtime_inventory(request).workspace_volume_gateway
    if gateway is None:
        raise HTTPException(status_code=503, detail="application composition is not ready")
    return gateway


TurnCoordinatorDep = Annotated[TurnCoordinator, Depends(get_turn_coordinator)]
ArtifactReaderDep = Annotated[ArtifactReader, Depends(get_artifact_reader)]
AttachmentLifecycleDep = Annotated[AttachmentLifecycle, Depends(get_attachment_lifecycle)]
SessionCatalogDep = Annotated[SessionCatalog, Depends(get_session_catalog)]
TurnLifecycleDep = Annotated[TurnLifecycle, Depends(get_turn_lifecycle)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SkillCatalogDep = Annotated[SkillCatalog, Depends(get_skill_catalog)]
ConfigPolicyDep = Annotated[ConfigPolicyService, Depends(get_config_policy)]
WorkspaceFileServiceDep = Annotated[WorkspaceFileService, Depends(get_workspace_file_service)]
WorkspaceVolumeGatewayDep = Annotated[WorkspaceVolumeGateway, Depends(get_workspace_volume_gateway)]
LocalScopeDep = Annotated[LocalScope, Depends(get_local_scope)]

__all__ = [
    "ArtifactReaderDep",
    "AttachmentLifecycleDep",
    "ConfigPolicyDep",
    "LocalScopeDep",
    "SessionCatalogDep",
    "SettingsDep",
    "SkillCatalogDep",
    "TurnCoordinatorDep",
    "TurnLifecycleDep",
    "WorkspaceFileServiceDep",
    "WorkspaceVolumeGatewayDep",
    "require_loopback_client",
]
