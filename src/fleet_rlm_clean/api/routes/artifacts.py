"""POST/GET /api/artifacts — durable outputs by opaque ID (no path leaks)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import ArtifactResponse, CreateArtifactRequest
from fleet_rlm_clean.artifacts.errors import ArtifactNotFoundError, ArtifactValidationError
from fleet_rlm_clean.artifacts.models import ArtifactRef
from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.paths import volume_paths_from_settings
from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
from fleet_rlm_clean.sessions.repository import SessionRepository

router = APIRouter(tags=["artifacts"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings()


def _workspace_volume_mirror(request: Request, settings: Settings) -> HostVolumeMirror:
    mirror = getattr(request.app.state, "workspace_volume_mirror", None)
    if mirror is not None:
        return mirror
    upload_root = settings.upload_root or str(Path.cwd() / ".fleet_clean_uploads")
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(settings),
    )
    request.app.state.workspace_volume_mirror = mirror
    return mirror


def get_artifact_store(request: Request) -> LocalArtifactStore:
    store = getattr(request.app.state, "artifact_store", None)
    if store is not None:
        return store
    from fleet_rlm_clean.composition import is_live_mode

    if is_live_mode(request.app):
        raise HTTPException(status_code=503, detail="live composition is not ready")
    settings = _settings(request)
    if settings.artifact_root:
        root = settings.artifact_root
    elif settings.upload_root:
        root = str(Path(settings.upload_root).parent / "artifacts")
    else:
        root = str(Path.cwd() / ".fleet_clean_artifacts")
    mirror = _workspace_volume_mirror(request, settings)
    store = LocalArtifactStore(
        root,
        max_bytes=settings.max_artifact_bytes,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    request.app.state.artifact_store = store
    return store


def _session_repository(request: Request) -> SessionRepository | None:
    return getattr(request.app.state, "session_repository", None)


def _to_response(ref: ArtifactRef) -> ArtifactResponse:
    return ArtifactResponse(
        id=ref.id,
        session_id=ref.session_id,
        run_id=ref.run_id,
        kind=ref.kind,
        title=ref.title,
        media_type=ref.media_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
    )


@router.post("/api/artifacts", response_model=ArtifactResponse)
async def create_artifact(
    body: CreateArtifactRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
) -> ArtifactResponse:
    """Create one durable artifact; return opaque metadata only.

    Session and Run must belong to the caller and match each other (404 otherwise).
    """
    repo = _session_repository(request)
    if repo is None:
        raise HTTPException(status_code=503, detail="database not configured")
    owned = await repo.session_run_owned(
        session_id=body.session_id,
        run_id=body.run_id,
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    if not owned:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        ref = store.create(
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            session_id=body.session_id,
            run_id=body.run_id,
            kind=body.kind,
            content=body.content,
            title=body.title,
        )
    except ArtifactValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_response(ref)


@router.get("/api/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: Annotated[LocalArtifactStore, Depends(get_artifact_store)],
) -> ArtifactResponse:
    try:
        ref = store.get(
            artifact_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    return _to_response(ref)
