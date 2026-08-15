"""GET /api/artifacts/{id} — committed durable output by opaque ID."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Response

from fleet_rlm.api.dependencies import ArtifactReaderDep, LocalScopeDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import ArtifactResponse
from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
from fleet_rlm.posthog_client import get_client, get_distinct_id

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
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
    "/{artifact_id}",
    response_model=ArtifactResponse,
    operation_id="get_artifact",
)
async def get_artifact(
    artifact_id: UUID,
    identity: LocalScopeDep,
    reader: ArtifactReaderDep,
) -> ArtifactResponse:
    try:
        ref = await reader.metadata(
            ArtifactAccess(identity.user_id, identity.workspace_id),
            artifact_id,
        )
    except ArtifactNotFoundError as exc:
        raise http_error(404, "artifact_not_found", "Artifact not found") from exc
    except Exception as exc:
        raise http_error(503, "artifact_unavailable", "Artifact storage is unavailable") from exc
    return _to_response(ref)


@router.get(
    "/{artifact_id}/content",
    response_class=Response,
    operation_id="download_artifact",
    responses={
        200: {
            "description": "Artifact bytes with integrity headers",
            "headers": {
                "Content-Disposition": {"schema": {"type": "string"}},
                "ETag": {
                    "description": 'SHA-256 of the artifact bytes, quoted (e.g. "hex")',
                    "schema": {"type": "string"},
                },
                "Content-Length": {"schema": {"type": "integer"}},
                "X-Content-Type-Options": {"schema": {"type": "string"}},
            },
        },
        404: {"description": "Artifact not found"},
        503: {"description": "Artifact storage is unavailable"},
    },
)
async def download_artifact(
    artifact_id: UUID,
    identity: LocalScopeDep,
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
        raise http_error(404, "artifact_not_found", "Artifact not found") from exc
    except Exception as exc:
        raise http_error(503, "artifact_unavailable", "Artifact storage is unavailable") from exc

    extension = {"text": ".txt", "markdown": ".md", "json": ".json"}[ref.kind]
    stem = _SAFE_FILENAME.sub("-", ref.title or "artifact").strip(".-") or "artifact"
    filename = f"{stem}{extension}"
    ph = get_client()
    if ph is not None:
        ph.capture(
            distinct_id=get_distinct_id(),
            event="artifact_downloaded",
            properties={
                "workspace_id": str(identity.workspace_id),
                "artifact_id": str(artifact_id),
                "artifact_kind": ref.kind,
                "artifact_byte_size": ref.byte_size,
            },
        )
    return Response(
        content=data,
        media_type=ref.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "ETag": f'"{ref.checksum_sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
