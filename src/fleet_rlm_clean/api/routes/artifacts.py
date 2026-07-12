"""GET /api/artifacts/{id} — committed durable output by opaque ID."""

from __future__ import annotations

import inspect
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm_clean.api.dependencies import ArtifactStoreDep
from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import ArtifactResponse
from fleet_rlm_clean.artifacts.errors import ArtifactNotFoundError
from fleet_rlm_clean.artifacts.models import ArtifactRef

router = APIRouter(tags=["artifacts"])


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


@router.get("/api/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    store: ArtifactStoreDep,
) -> ArtifactResponse:
    try:
        result = store.get(
            artifact_id,
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
        ref = await result if inspect.isawaitable(result) else result
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="artifact storage unavailable") from exc
    return _to_response(ref)
