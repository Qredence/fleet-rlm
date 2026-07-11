"""Artifact approval, activation, and rollback endpoints for Phase 8."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal, cast

from fastapi import APIRouter, HTTPException
from fastapi import Path as ApiPath

from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

from ...dependencies import ConfigDepsDep, HTTPIdentityDep, PersistenceDep
from ...schemas.optimization import (
    OptimizationArtifactVersionResponse,
    OptimizationTargetActivationResponse,
)
from ._deps import AUTH_ERROR_RESPONSES, OpenAPIResponses, _resolve_persisted_identity, parse_run_uuid

logger = logging.getLogger(__name__)

router = APIRouter()


def _iso(value: object) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())
    return str(value)


def _artifact_response(row: object) -> OptimizationArtifactVersionResponse:
    target_kind = str(getattr(row, "target_kind", "module"))
    if target_kind not in {"module", "skill"}:
        target_kind = "module"
    return OptimizationArtifactVersionResponse(
        id=str(getattr(row, "id")),
        optimization_run_id=str(getattr(row, "optimization_run_id")),
        target_kind=cast(Literal["module", "skill"], target_kind),
        target_id=str(getattr(row, "target_id")),
        artifact_kind=str(getattr(row, "artifact_kind")),
        artifact_path=str(getattr(row, "artifact_path")),
        artifact_sha256=str(getattr(row, "artifact_sha256")),
        status=str(getattr(row, "status")),
        approved_at=_iso(getattr(row, "approved_at", None)),
        activated_at=_iso(getattr(row, "activated_at", None)),
        created_at=_iso(getattr(row, "created_at", None)) or "",
    )


def _parse_uuid(value: str, *, detail: str) -> uuid.UUID:
    try:
        return parse_run_uuid(value)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


@router.get(
    "/runs/{run_id}/artifact",
    response_model=OptimizationArtifactVersionResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Artifact not found."},
            501: {"description": "Requires managed Postgres persistence."},
        },
    ),
)
async def get_run_artifact(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    run_id: Annotated[str, ApiPath(description="Optimization run identifier.")],
) -> OptimizationArtifactVersionResponse:
    """Return the persisted artifact version for a completed run, if any."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    try:
        row = await persistence.get_optimization_artifact_for_run(
            tenant_id=persisted_identity.tenant_id,
            run_id=parse_run_uuid(run_id),
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact version not found for run.")
    return _artifact_response(row)


@router.post(
    "/artifacts/{artifact_version_id}/approve",
    response_model=OptimizationArtifactVersionResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Artifact not found."},
            409: {"description": "Artifact cannot be approved."},
            501: {"description": "Requires managed Postgres persistence."},
        },
    ),
)
async def approve_artifact_version(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    artifact_version_id: Annotated[str, ApiPath(description="Artifact version identifier.")],
) -> OptimizationArtifactVersionResponse:
    """Human-approve a candidate artifact. Does not activate runtime behavior."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    artifact_uuid = _parse_uuid(artifact_version_id, detail="Artifact version not found.")
    try:
        row = await persistence.approve_optimization_artifact_version(
            tenant_id=persisted_identity.tenant_id,
            artifact_version_id=artifact_uuid,
            approved_by_user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact version not found.")
    return _artifact_response(row)


@router.post(
    "/artifacts/{artifact_version_id}/activate",
    response_model=OptimizationTargetActivationResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "Artifact not found."},
            409: {"description": "Artifact cannot be activated."},
            501: {"description": "Requires managed Postgres persistence."},
        },
    ),
)
async def activate_artifact_version(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    artifact_version_id: Annotated[str, ApiPath(description="Approved artifact version identifier.")],
) -> OptimizationTargetActivationResponse:
    """Atomically activate an approved artifact for its Managed Target in this workspace."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    artifact_uuid = _parse_uuid(artifact_version_id, detail="Artifact version not found.")
    try:
        activation = await persistence.activate_optimization_target(
            tenant_id=persisted_identity.tenant_id,
            artifact_version_id=artifact_uuid,
            activated_by_user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
        artifact = await persistence.get_optimization_artifact_version(
            tenant_id=persisted_identity.tenant_id,
            artifact_version_id=artifact_uuid,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    target_kind = str(getattr(activation, "target_kind", "module"))
    if target_kind not in {"module", "skill"}:
        target_kind = "module"
    return OptimizationTargetActivationResponse(
        target_kind=cast(Literal["module", "skill"], target_kind),
        target_id=str(getattr(activation, "target_id")),
        active_artifact_version_id=str(getattr(activation, "active_artifact_version_id")),
        previous_artifact_version_id=(
            str(prev) if (prev := getattr(activation, "previous_artifact_version_id", None)) is not None else None
        ),
        active_artifact=_artifact_response(artifact) if artifact is not None else None,
        updated_at=_iso(getattr(activation, "updated_at", None)) or "",
    )


@router.post(
    "/targets/{target_kind}/{target_id}/rollback",
    response_model=OptimizationTargetActivationResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "No activation or previous version to roll back."},
            501: {"description": "Requires managed Postgres persistence."},
        },
    ),
)
async def rollback_target_activation(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    target_kind: Annotated[Literal["module", "skill"], ApiPath(description="Managed target kind.")],
    target_id: Annotated[str, ApiPath(description="Managed target identifier.")],
) -> OptimizationTargetActivationResponse:
    """Roll back to the previous retained artifact version for a Managed Target."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    try:
        activation = await persistence.rollback_optimization_target(
            tenant_id=persisted_identity.tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            rolled_back_by_user_id=persisted_identity.user_id,
            workspace_id=persisted_identity.workspace_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if activation is None:
        raise HTTPException(status_code=404, detail="No previous activation available to roll back.")

    active_id = getattr(activation, "active_artifact_version_id")
    artifact = await persistence.get_optimization_artifact_version(
        tenant_id=persisted_identity.tenant_id,
        artifact_version_id=active_id,
        workspace_id=persisted_identity.workspace_id,
        created_by_user_id=persisted_identity.user_id,
    )
    return OptimizationTargetActivationResponse(
        target_kind=target_kind,
        target_id=target_id,
        active_artifact_version_id=str(active_id),
        previous_artifact_version_id=None,
        active_artifact=_artifact_response(artifact) if artifact is not None else None,
        updated_at=_iso(getattr(activation, "updated_at", None)) or "",
    )


@router.get(
    "/targets/{target_kind}/{target_id}/activation",
    response_model=OptimizationTargetActivationResponse,
    responses=cast(
        OpenAPIResponses,
        {
            **AUTH_ERROR_RESPONSES,
            404: {"description": "No activation pointer for this target."},
            501: {"description": "Requires managed Postgres persistence."},
        },
    ),
)
async def get_target_activation(
    config_deps: ConfigDepsDep,
    identity: HTTPIdentityDep,
    persistence: PersistenceDep,
    target_kind: Annotated[Literal["module", "skill"], ApiPath(description="Managed target kind.")],
    target_id: Annotated[str, ApiPath(description="Managed target identifier.")],
) -> OptimizationTargetActivationResponse:
    """Return the workspace activation pointer for a Managed Target."""
    persisted_identity = await _resolve_persisted_identity(
        config_deps=config_deps,
        persistence=persistence,
        identity=identity,
    )
    try:
        activation, artifact = await persistence.get_target_activation(
            tenant_id=persisted_identity.tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            workspace_id=persisted_identity.workspace_id,
            created_by_user_id=persisted_identity.user_id,
        )
    except UnsupportedLocalCapabilityError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if activation is None:
        raise HTTPException(status_code=404, detail="No activation for this target in the workspace.")
    return OptimizationTargetActivationResponse(
        target_kind=target_kind,
        target_id=target_id,
        active_artifact_version_id=str(getattr(activation, "active_artifact_version_id")),
        previous_artifact_version_id=(
            str(prev) if (prev := getattr(activation, "previous_artifact_version_id", None)) is not None else None
        ),
        active_artifact=_artifact_response(artifact) if artifact is not None else None,
        updated_at=_iso(getattr(activation, "updated_at", None)) or "",
    )


__all__ = ["router"]
