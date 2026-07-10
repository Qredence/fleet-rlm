"""File upload endpoints (Phase 5 attachment staging slice)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from fleet_rlm.files.upload_staging import (
    UploadSafetyError,
    attachment_owner_scope,
    stage_uploaded_file_to_volume,
)
from fleet_rlm.integrations.daytona.volumes import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH

from ..dependencies import HTTPIdentityDep
from ..schemas.files import FileUploadResponse, UploadedFileMetadata

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/upload",
    response_model=FileUploadResponse,
)
async def upload_file(
    identity: HTTPIdentityDep,
    session_id: Annotated[str, Form(description="Session identifier to scope attachment staging.")],
    file: Annotated[UploadFile, File(description="File to upload (single attachment).")],
) -> FileUploadResponse:
    """Upload a file and stage it into durable session-scoped storage."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    try:
        staged = stage_uploaded_file_to_volume(
            volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
            session_id=session_id,
            filename=file.filename,
            content_type=file.content_type,
            stream=file.file,
            owner_scope=attachment_owner_scope(
                tenant_claim=identity.tenant_claim,
                user_claim=identity.user_claim,
            ),
        )
    except UploadSafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to stage uploaded file.") from exc

    uploaded = UploadedFileMetadata(
        filename=staged.attachment.filename,
        content_type=staged.attachment.mime_type,
        size_bytes=staged.attachment.size_bytes,
        checksum_sha256=staged.attachment.checksum,
        created_at=datetime.now(timezone.utc),
    )
    return FileUploadResponse(
        attachment=staged.attachment,
        uploaded=uploaded,
    )


__all__ = ["router"]
