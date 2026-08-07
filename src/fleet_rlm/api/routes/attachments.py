"""Canonical Attachment upload and metadata routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile

from fleet_rlm.api.dependencies import AttachmentLifecycleDep, LocalScopeDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import AttachmentResponse
from fleet_rlm.files.errors import AttachmentError, AttachmentNotFoundError
from fleet_rlm.files.models import AttachmentAccess, AttachmentUpload

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post(
    "",
    response_model=AttachmentResponse,
    status_code=201,
    operation_id="create_attachment",
)
async def upload_attachment(
    attachment: Annotated[UploadFile, File()],
    identity: LocalScopeDep,
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
        raise http_error(400, "attachment_invalid", str(exc)) from exc
    except Exception as exc:
        raise http_error(503, "attachment_unavailable", "Attachment storage is unavailable") from exc
    return AttachmentResponse.model_validate(ref, from_attributes=True)


@router.get(
    "/{attachment_id}",
    response_model=AttachmentResponse,
    operation_id="get_attachment",
)
async def get_attachment(
    attachment_id: UUID,
    identity: LocalScopeDep,
    lifecycle: AttachmentLifecycleDep,
) -> AttachmentResponse:
    try:
        refs = await lifecycle.metadata(
            AttachmentAccess(identity.user_id, identity.workspace_id),
            (attachment_id,),
        )
        ref = refs[0]
    except AttachmentNotFoundError as exc:
        raise http_error(404, "attachment_not_found", "Attachment not found") from exc
    except Exception as exc:
        raise http_error(503, "attachment_unavailable", "Attachment storage is unavailable") from exc
    return AttachmentResponse.model_validate(ref, from_attributes=True)
