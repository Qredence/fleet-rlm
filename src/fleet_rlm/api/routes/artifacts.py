"""GET /api/artifacts/{id} — committed durable output by opaque ID."""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from fleet_rlm.api.dependencies import ArtifactReaderDep
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.api.schemas import ArtifactResponse
from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef

router = APIRouter(tags=["artifacts"])
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


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


@router.get(
    "/api/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
    operation_id="get_artifact",
)
async def get_artifact(
    artifact_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    reader: ArtifactReaderDep,
) -> ArtifactResponse:
    try:
        ref = await reader.metadata(
            ArtifactAccess(identity.user_id, identity.workspace_id),
            artifact_id,
        )
    except ArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_not_found", "message": "Artifact not found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "artifact_unavailable", "message": "Artifact storage is unavailable"},
        ) from exc
    return _to_response(ref)


@router.get(
    "/api/artifacts/{artifact_id}/content",
    response_class=Response,
    operation_id="download_artifact",
)
async def download_artifact(
    artifact_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    reader: ArtifactReaderDep,
) -> Response:
    try:
        content = await reader.content(
            ArtifactAccess(identity.user_id, identity.workspace_id),
            artifact_id,
        )
        ref = content.metadata
        data = content.data
    except ArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_not_found", "message": "Artifact not found"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "artifact_unavailable", "message": "Artifact storage is unavailable"},
        ) from exc

    extension = {"text": ".txt", "markdown": ".md", "json": ".json"}[ref.kind]
    stem = _SAFE_FILENAME.sub("-", ref.title or "artifact").strip(".-") or "artifact"
    filename = f"{stem}{extension}"
    return Response(
        content=data,
        media_type=ref.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "ETag": f'"{ref.checksum_sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
