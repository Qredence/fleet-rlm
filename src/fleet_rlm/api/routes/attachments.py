"""Canonical Attachment upload and metadata routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from fleet_rlm.api.dependencies import AttachmentLifecycleDep
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.api.schemas import AttachmentResponse
from fleet_rlm.files.errors import AttachmentError, AttachmentNotFoundError
from fleet_rlm.files.models import AttachmentAccess, AttachmentUpload

router = APIRouter(tags=["attachments"])


@router.post(
    "/api/attachments",
    response_model=AttachmentResponse,
    status_code=201,
    operation_id="create_attachment",
)
async def upload_attachment(
    attachment: Annotated[UploadFile, File()],
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    lifecycle: AttachmentLifecycleDep,
) -> AttachmentResponse:
    try:
        ref = await lifecycle.upload(
            AttachmentAccess(identity.user_id, identity.workspace_id),
            AttachmentUpload(
                filename=attachment.filename or "upload.bin",
                content_type=attachment.content_type,
                source=attachment,
            ),
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=400, detail={"code": "attachment_invalid", "message": str(exc)}) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "attachment_unavailable", "message": "Attachment storage is unavailable"},
        ) from exc
    return AttachmentResponse.model_validate(ref, from_attributes=True)


@router.get(
    "/api/attachments/{attachment_id}",
    response_model=AttachmentResponse,
    operation_id="get_attachment",
)
async def get_attachment(
    attachment_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    lifecycle: AttachmentLifecycleDep,
) -> AttachmentResponse:
    try:
        refs = await lifecycle.metadata(
            AttachmentAccess(identity.user_id, identity.workspace_id),
            (attachment_id,),
        )
        ref = refs[0]
    except AttachmentNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "attachment_not_found", "message": "Attachment not found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "attachment_unavailable", "message": "Attachment storage is unavailable"},
        ) from exc
    return AttachmentResponse.model_validate(ref, from_attributes=True)
