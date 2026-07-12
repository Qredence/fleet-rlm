"""POST /api/files — upload and authorize attachment metadata (no path leaks).

Staging is Turn-internal only (AttachmentStager); there is no public stage route.
"""

from __future__ import annotations

import inspect
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from fleet_rlm_clean.api.dependencies import AttachmentStoreDep, SettingsDep
from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import AttachmentResponse
from fleet_rlm_clean.files.errors import AttachmentNotFoundError, AttachmentValidationError

router = APIRouter(tags=["files"])


@router.post("/api/files", response_model=AttachmentResponse)
async def upload_file(
    file: Annotated[UploadFile, File()],
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: AttachmentStoreDep,
    settings: SettingsDep,
) -> AttachmentResponse:
    """Upload one file; return opaque AttachmentRef metadata only."""
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(min(1024 * 1024, settings.max_upload_bytes + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_upload_bytes:
                raise AttachmentValidationError(f"attachment exceeds max size of {settings.max_upload_bytes} bytes")
            chunks.append(chunk)
        data = b"".join(chunks)
        result = store.upload(
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
            filename=file.filename or "upload.bin",
            content_type=file.content_type,
            data=data,
        )
        ref = await result if inspect.isawaitable(result) else result
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="attachment storage unavailable") from exc
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
    store: AttachmentStoreDep,
) -> AttachmentResponse:
    try:
        result = store.get(file_id, user_id=identity.user_id, workspace_id=identity.workspace_id)
        ref = await result if inspect.isawaitable(result) else result
    except AttachmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="attachment storage unavailable") from exc
    return AttachmentResponse(
        id=ref.id,
        filename=ref.filename,
        content_type=ref.content_type,
        byte_size=ref.byte_size,
        checksum_sha256=ref.checksum_sha256,
    )
