"""FastAPI dependency aliases for lifespan-composed Fleet RLM modules."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from fleet_rlm.api.errors import http_error
from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments.lifecycle import AttachmentLifecycle
from fleet_rlm.chat.run_lifecycle import RunLifecycle
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.composition.inventory import RuntimeInventory, get_runtime_inventory
from fleet_rlm.config.policy import ConfigPolicyService
from fleet_rlm.config.settings import Settings
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.workspace.storage import WorkspaceVolumeGateway
from fleet_rlm.workspace.workspace import WorkspaceFileService


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


def _composition_unavailable() -> HTTPException:
    """Closed-contract 503 raised while lifespan composition is incomplete."""
    return http_error(503, "turn_unavailable", "Service unavailable")


def get_ready_runtime_inventory(request: Request) -> RuntimeInventory:
    if not getattr(request.app.state, "composition_ready", False):
        raise _composition_unavailable()
    inventory = get_runtime_inventory(request.app)
    if inventory is None:
        raise _composition_unavailable()
    return inventory


def get_runtime_inventory_if_ready(request: Request) -> RuntimeInventory | None:
    """Return the composed inventory without failing pre-composition requests.

    Health probes must distinguish "process alive but not composed" from
    "composition complete", so they read composition readiness directly
    instead of sharing the closed 503 dependency used by serving routes.
    """
    if not getattr(request.app.state, "composition_ready", False):
        return None
    return get_runtime_inventory(request.app)


def get_turn_runtime(request: Request) -> TurnRuntime:
    runtime = get_ready_runtime_inventory(request).turn_runtime
    if runtime is None:
        raise _composition_unavailable()
    return runtime


def get_attachment_lifecycle(request: Request) -> AttachmentLifecycle:
    lifecycle = get_ready_runtime_inventory(request).attachment_lifecycle
    if lifecycle is None:
        raise _composition_unavailable()
    return lifecycle


def get_artifact_reader(request: Request) -> ArtifactReader:
    reader = get_ready_runtime_inventory(request).artifact_reader
    if reader is None:
        raise _composition_unavailable()
    return reader


def get_session_catalog(request: Request) -> SessionCatalog:
    catalog = get_ready_runtime_inventory(request).session_catalog
    if catalog is None:
        raise _composition_unavailable()
    return catalog


def get_session_runtime_registry(request: Request) -> SessionRLMRegistry | None:
    """Return the process-local resident runtime registry when composed."""
    return get_ready_runtime_inventory(request).session_runtime_registry


def get_session_prewarm(request: Request) -> Callable[[UUID, UUID, UUID], asyncio.Task[None]] | None:
    """Return a fire-and-forget Session sandbox pre-warm trigger, if composed.

    The returned callable schedules a background acquisition of the
    provider Sandbox and canonical Volume layout for a newly created
    Session, so the first Turn reuses a warm binding instead of paying
    sandbox creation and layout on the user-visible path. Failures inside
    the background task are suppressed: a pre-warm is an optimization, and
    the first Turn acquires normally when no warm binding exists.
    """
    manager = get_ready_runtime_inventory(request).session_manager
    if manager is None:
        return None
    prewarm = manager.prewarm_session

    def schedule(session_id: UUID, user_id: UUID, workspace_id: UUID) -> asyncio.Task[None]:
        async def run_prewarm() -> None:
            try:
                await prewarm(session_id, user_id=user_id, workspace_id=workspace_id)
            except asyncio.CancelledError:
                raise
            except BaseException:
                # Suppressed by design: the first Turn retries acquisition.
                pass

        return asyncio.create_task(run_prewarm(), name=f"fleet-session-prewarm-{session_id}")

    return schedule


def get_run_lifecycle(request: Request) -> RunLifecycle:
    lifecycle = get_ready_runtime_inventory(request).run_lifecycle
    if lifecycle is None:
        raise _composition_unavailable()
    return lifecycle


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_skill_catalog(request: Request) -> SkillCatalog:
    catalog = getattr(request.app.state, "skill_catalog", None)
    if not isinstance(catalog, SkillCatalog):
        # The bundled catalog is installed by create_app() independently of
        # lifespan composition; its absence still surfaces as the closed 503.
        raise _composition_unavailable()
    return catalog


def get_config_policy(request: Request) -> ConfigPolicyService:
    policy = get_ready_runtime_inventory(request).config_policy
    if not isinstance(policy, ConfigPolicyService):
        raise _composition_unavailable()
    return policy


def get_workspace_file_service(request: Request) -> WorkspaceFileService:
    service = get_ready_runtime_inventory(request).workspace_file_service
    if not isinstance(service, WorkspaceFileService):
        raise _composition_unavailable()
    return service


def get_workspace_volume_gateway(request: Request) -> WorkspaceVolumeGateway:
    gateway = get_ready_runtime_inventory(request).workspace_volume_gateway
    if gateway is None:
        raise _composition_unavailable()
    return gateway


TurnRuntimeDep = Annotated[TurnRuntime, Depends(get_turn_runtime)]
ArtifactReaderDep = Annotated[ArtifactReader, Depends(get_artifact_reader)]
AttachmentLifecycleDep = Annotated[AttachmentLifecycle, Depends(get_attachment_lifecycle)]
SessionCatalogDep = Annotated[SessionCatalog, Depends(get_session_catalog)]
SessionRuntimeRegistryDep = Annotated[SessionRLMRegistry | None, Depends(get_session_runtime_registry)]
SessionPrewarmDep = Annotated[Callable[[UUID, UUID, UUID], asyncio.Task[None]] | None, Depends(get_session_prewarm)]
RunLifecycleDep = Annotated[RunLifecycle, Depends(get_run_lifecycle)]
RuntimeInventoryIfReadyDep = Annotated[RuntimeInventory | None, Depends(get_runtime_inventory_if_ready)]
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
    "RunLifecycleDep",
    "RuntimeInventoryIfReadyDep",
    "SessionCatalogDep",
    "SessionPrewarmDep",
    "SessionRuntimeRegistryDep",
    "SettingsDep",
    "SkillCatalogDep",
    "TurnRuntimeDep",
    "WorkspaceFileServiceDep",
    "WorkspaceVolumeGatewayDep",
    "require_loopback_client",
]
