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
from fleet_rlm.composition.inventory import RuntimeInventory, RuntimeInventoryError, get_runtime_inventory
from fleet_rlm.config.policy import ConfigPolicyService
from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.feedback import TraceFeedbackService
from fleet_rlm.observability.mlflow import MLflowRuntime
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.lifecycle import SessionLifecycle
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
    try:
        return get_ready_runtime_inventory(request).require_turn_runtime()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_attachment_lifecycle(request: Request) -> AttachmentLifecycle:
    try:
        return get_ready_runtime_inventory(request).require_attachment_lifecycle()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_artifact_reader(request: Request) -> ArtifactReader:
    try:
        return get_ready_runtime_inventory(request).require_artifact_reader()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_session_catalog(request: Request) -> SessionCatalog:
    try:
        return get_ready_runtime_inventory(request).require_session_catalog()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_session_lifecycle(request: Request) -> SessionLifecycle:
    try:
        return get_ready_runtime_inventory(request).require_session_lifecycle()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_session_prewarm(request: Request) -> Callable[[UUID, UUID, UUID], asyncio.Task[None]] | None:
    """Return the composed Session manager's pre-warm scheduler, if present.

    Scheduling and task retention live in the session manager; this dependency
    only retrieves the callable so routes stay transport-thin.
    """
    manager = get_ready_runtime_inventory(request).session_manager
    if manager is None:
        return None
    return manager.schedule_prewarm


def get_run_lifecycle(request: Request) -> RunLifecycle:
    try:
        return get_ready_runtime_inventory(request).require_run_lifecycle()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_trace_feedback_service(request: Request) -> TraceFeedbackService:
    """Return the application-owned MLflow feedback service."""
    service = getattr(request.app.state, "trace_feedback_service", None)
    if not isinstance(service, TraceFeedbackService):
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable")
    return service


def get_mlflow_runtime(request: Request) -> MLflowRuntime:
    """Return the application-owned MLflow lifecycle for synchronous SDK work."""
    runtime = getattr(request.app.state, "mlflow_runtime", None)
    if not isinstance(runtime, MLflowRuntime):
        raise http_error(503, "trace_feedback_unavailable", "Trace feedback is unavailable")
    return runtime


def get_skill_catalog(request: Request) -> SkillCatalog:
    catalog = getattr(request.app.state, "skill_catalog", None)
    if not isinstance(catalog, SkillCatalog):
        # The bundled catalog is installed by create_app() independently of
        # lifespan composition; its absence still surfaces as the closed 503.
        raise _composition_unavailable()
    return catalog


def get_config_policy(request: Request) -> ConfigPolicyService:
    try:
        return get_ready_runtime_inventory(request).require_config_policy()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_workspace_file_service(request: Request) -> WorkspaceFileService:
    try:
        return get_ready_runtime_inventory(request).require_workspace_file_service()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


def get_workspace_volume_gateway(request: Request) -> WorkspaceVolumeGateway:
    try:
        return get_ready_runtime_inventory(request).require_workspace_volume_gateway()
    except RuntimeInventoryError:
        raise _composition_unavailable() from None


TurnRuntimeDep = Annotated[TurnRuntime, Depends(get_turn_runtime)]
ArtifactReaderDep = Annotated[ArtifactReader, Depends(get_artifact_reader)]
AttachmentLifecycleDep = Annotated[AttachmentLifecycle, Depends(get_attachment_lifecycle)]
SessionCatalogDep = Annotated[SessionCatalog, Depends(get_session_catalog)]
SessionLifecycleDep = Annotated[SessionLifecycle, Depends(get_session_lifecycle)]
SessionPrewarmDep = Annotated[Callable[[UUID, UUID, UUID], asyncio.Task[None]] | None, Depends(get_session_prewarm)]
RunLifecycleDep = Annotated[RunLifecycle, Depends(get_run_lifecycle)]
RuntimeInventoryIfReadyDep = Annotated[RuntimeInventory | None, Depends(get_runtime_inventory_if_ready)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
TraceFeedbackServiceDep = Annotated[TraceFeedbackService, Depends(get_trace_feedback_service)]
MLflowRuntimeDep = Annotated[MLflowRuntime, Depends(get_mlflow_runtime)]
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
    "MLflowRuntimeDep",
    "RunLifecycleDep",
    "RuntimeInventoryIfReadyDep",
    "SessionCatalogDep",
    "SessionLifecycleDep",
    "SessionPrewarmDep",
    "SettingsDep",
    "SkillCatalogDep",
    "TraceFeedbackServiceDep",
    "TurnRuntimeDep",
    "WorkspaceFileServiceDep",
    "WorkspaceVolumeGatewayDep",
    "require_loopback_client",
]
