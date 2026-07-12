"""POST /api/files — upload and authorize attachment metadata (no path leaks)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import (
    AttachmentResponse,
    StageAttachmentRequest,
    StagedAttachmentResponse,
)
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.paths import volume_paths_from_settings
from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror
from fleet_rlm_clean.files.errors import AttachmentNotFoundError, AttachmentValidationError
from fleet_rlm_clean.files.staging import AttachmentStager
from fleet_rlm_clean.files.uploads import LocalAttachmentStore

router = APIRouter(tags=["files"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or Settings()


def _workspace_volume_mirror(request: Request, settings: Settings) -> HostVolumeMirror:
    mirror = getattr(request.app.state, "workspace_volume_mirror", None)
    if mirror is not None:
        return mirror
    upload_root = settings.upload_root or str(Path.cwd() / ".fleet_clean_uploads")
    # Offline / process-local stand-in for Workspace Volume Scope (not production SoT).
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(settings),
    )
    request.app.state.workspace_volume_mirror = mirror
    return mirror


def get_attachment_store(request: Request) -> LocalAttachmentStore:
    store = getattr(request.app.state, "attachment_store", None)
    if store is not None:
        return store
    settings = _settings(request)
    root = settings.upload_root or str(Path.cwd() / ".fleet_clean_uploads")
    mirror = _workspace_volume_mirror(request, settings)
    store = LocalAttachmentStore(
        root,
        max_bytes=settings.max_upload_bytes,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    request.app.state.attachment_store = store
    return store


def get_attachment_stager(
    request: Request,
    store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
) -> AttachmentStager:
    stager = getattr(request.app.state, "attachment_stager", None)
    if stager is not None:
        return stager
    settings = _settings(request)
    mirror = _workspace_volume_mirror(request, settings)
    stager = AttachmentStager(
        store,
        volume_fs=mirror,
        volume_paths=mirror.volume_paths,
    )
    request.app.state.attachment_stager = stager
    return stager


@router.post("/api/files", response_model=AttachmentResponse)
async def upload_file(
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
    file: UploadFile = File(...),
) -> AttachmentResponse:
    """Upload one file; return opaque AttachmentRef metadata only."""
    data = await file.read()
    try:
        ref = store.upload(
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            filename=file.filename or "upload.bin",
            content_type=file.content_type,
            data=data,
        )
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AttachmentResponse(
        id=ref.id,
        filename=ref.filename,
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
    )


@router.get("/api/files/{file_id}", response_model=AttachmentResponse)
async def get_file(
    file_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: Annotated[LocalAttachmentStore, Depends(get_attachment_store)],
) -> AttachmentResponse:
    try:
        ref = store.get(file_id, user_id=identity.user_id, workspace_id=identity.workspace_id)
    except AttachmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    return AttachmentResponse(
        id=ref.id,
        filename=ref.filename,
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
    )


@router.post("/api/files/{file_id}/stage", response_model=StagedAttachmentResponse)
async def stage_file(
    file_id: UUID,
    body: StageAttachmentRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    stager: Annotated[AttachmentStager, Depends(get_attachment_stager)],
) -> StagedAttachmentResponse:
    """Re-authorize and stage into a session/run Sandbox path (logical only)."""
    try:
        staged = stager.stage(
            file_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            session_id=body.session_id,
            run_id=body.run_id,
        )
    except AttachmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StagedAttachmentResponse(
        attachment_id=staged.attachment_id,
        sandbox_path=staged.sandbox_path,
    )
