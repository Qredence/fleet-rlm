"""Unified file, volume, artifact, and attachment routes."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile

from fleet_rlm.api.dependencies import (
    ArtifactReaderDep,
    AttachmentLifecycleDep,
    LocalScopeDep,
    SettingsDep,
    WorkspaceFileServiceDep,
    WorkspaceVolumeGatewayDep,
)
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import (
    ArtifactResponse,
    AttachmentResponse,
    VolumeTreeResponse,
    WorkspaceFileAppendRequest,
    WorkspaceFileDeleteRequest,
    WorkspaceFileDeleteResponse,
    WorkspaceFileEntryResponse,
    WorkspaceFileListResponse,
    WorkspaceFilePatchRequest,
    WorkspaceFileReadResponse,
    WorkspaceFileWriteRequest,
)
from fleet_rlm.artifacts.errors import ArtifactNotFoundError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
from fleet_rlm.attachments.errors import AttachmentError, AttachmentNotFoundError, AttachmentStorageError
from fleet_rlm.attachments.models import AttachmentAccess, AttachmentUpload
from fleet_rlm.observability.posthog import capture
from fleet_rlm.workspace.workspace import (
    MAX_PUBLIC_LIST_LIMIT,
    MAX_PUBLIC_READ_CHARS,
    WorkspaceFileConflictError,
    WorkspaceFileEntry,
)

# ---------------------------------------------------------------------------
# Workspace Files Router (/api/files)
# ---------------------------------------------------------------------------

workspace_files_router = APIRouter(prefix="/api/files", tags=["workspace-files"])


def _entry(value: WorkspaceFileEntry) -> WorkspaceFileEntryResponse:
    return WorkspaceFileEntryResponse.model_validate(value, from_attributes=True)


def _raise_public_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, (WorkspaceFileConflictError, FileExistsError)):
        raise http_error(
            409,
            "workspace_file_conflict",
            "Workspace file precondition did not match",
        ) from exc
    if isinstance(exc, FileNotFoundError):
        raise http_error(404, "workspace_file_not_found", "Workspace file not found") from exc
    if isinstance(exc, (ValueError, IsADirectoryError, NotADirectoryError)):
        raise http_error(400, "workspace_file_invalid", "Workspace file request is invalid") from exc
    raise http_error(503, "workspace_files_unavailable", "Workspace files are unavailable") from exc


_READ_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Workspace file request is invalid"},
    404: {"description": "Workspace file not found"},
    503: {"description": "Workspace files are unavailable"},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_READ_ERRORS,
    409: {"description": "Workspace file precondition did not match"},
}


@workspace_files_router.get(
    "",
    response_model=WorkspaceFileListResponse,
    operation_id="list_workspace_files_api",
    responses=_READ_ERRORS,
)
async def list_workspace_files(
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
    path: Annotated[str, Query(description="Workspace-relative path")] = ".",
    limit: Annotated[int, Query(ge=1, le=MAX_PUBLIC_LIST_LIMIT)] = MAX_PUBLIC_LIST_LIMIT,
    after: Annotated[str | None, Query()] = None,
) -> WorkspaceFileListResponse:
    try:
        listing = await service.list(
            identity.workspace_id,
            path,
            limit=limit,
            after=after,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return WorkspaceFileListResponse(
        entries=[_entry(value) for value in listing.entries],
        truncated=listing.truncated,
        next_cursor=listing.next_cursor,
    )


@workspace_files_router.get(
    "/stat",
    response_model=WorkspaceFileEntryResponse,
    operation_id="stat_workspace_file_api",
    responses=_READ_ERRORS,
)
async def stat_workspace_file(
    path: Annotated[str, Query()],
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileEntryResponse:
    try:
        value = await service.stat(identity.workspace_id, path)
        if value is None:
            raise FileNotFoundError(path)
    except Exception as exc:
        _raise_public_error(exc)
    return _entry(value)


@workspace_files_router.get(
    "/content",
    response_model=WorkspaceFileReadResponse,
    operation_id="read_workspace_file_api",
    responses=_READ_ERRORS,
)
async def read_workspace_file(
    path: Annotated[str, Query()],
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
    cursor: Annotated[str | None, Query()] = None,
    max_chars: Annotated[int, Query(ge=1, le=MAX_PUBLIC_READ_CHARS)] = MAX_PUBLIC_READ_CHARS,
) -> WorkspaceFileReadResponse:
    try:
        page = await service.read(
            identity.workspace_id,
            path,
            cursor=cursor,
            max_chars=max_chars,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return WorkspaceFileReadResponse(
        path=path,
        content=page.content,
        next_cursor=page.next_cursor,
        byte_size=page.byte_size,
        eof=page.eof,
    )


@workspace_files_router.put(
    "/content",
    response_model=WorkspaceFileEntryResponse,
    operation_id="write_workspace_file_api",
    responses=_WRITE_ERRORS,
)
async def write_workspace_file(
    body: WorkspaceFileWriteRequest,
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileEntryResponse:
    try:
        value = await service.write(
            identity.workspace_id,
            body.path,
            body.content,
            overwrite=body.overwrite,
            expected_sha256=body.expected_sha256,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return _entry(value)


@workspace_files_router.post(
    "/append",
    response_model=WorkspaceFileEntryResponse,
    operation_id="append_workspace_file_api",
    responses=_WRITE_ERRORS,
)
async def append_workspace_file(
    body: WorkspaceFileAppendRequest,
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileEntryResponse:
    try:
        value = await service.append(
            identity.workspace_id,
            body.path,
            body.content,
            expected_sha256=body.expected_sha256,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return _entry(value)


@workspace_files_router.delete(
    "/content",
    response_model=WorkspaceFileDeleteResponse,
    operation_id="delete_workspace_file_api",
    responses=_WRITE_ERRORS,
)
async def delete_workspace_file(
    body: WorkspaceFileDeleteRequest,
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileDeleteResponse:
    try:
        await service.delete(
            identity.workspace_id,
            body.path,
            expected_sha256=body.expected_sha256,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return WorkspaceFileDeleteResponse(path=body.path)


@workspace_files_router.patch(
    "/content",
    response_model=WorkspaceFileEntryResponse,
    operation_id="patch_workspace_file_api",
    responses=_WRITE_ERRORS,
)
async def patch_workspace_file(
    body: WorkspaceFilePatchRequest,
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileEntryResponse:
    try:
        value = await service.patch(
            identity.workspace_id,
            body.path,
            body.old,
            body.new,
            expected_sha256=body.expected_sha256,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return _entry(value)


# ---------------------------------------------------------------------------
# Volume Router (/api/volume)
# ---------------------------------------------------------------------------

volume_router = APIRouter(prefix="/api/volume", tags=["volume"])


@volume_router.get(
    "/tree",
    response_model=VolumeTreeResponse,
    operation_id="list_volume_tree_api",
    responses={
        400: {"description": "Volume tree request is invalid"},
        503: {"description": "Workspace Volume is unavailable"},
    },
)
async def list_volume_tree(
    identity: LocalScopeDep,
    gateway: WorkspaceVolumeGatewayDep,
    settings: SettingsDep,
    root: Annotated[str, Query(min_length=1, max_length=256)] = ".",
    max_depth: Annotated[int, Query(ge=1, le=32)] = 8,
    max_files: Annotated[int, Query(ge=1, le=10_000)] = 2_000,
) -> VolumeTreeResponse:
    try:
        mount = PurePosixPath(settings.volume_mount_path)
        requested = PurePosixPath(root)
        if "\x00" in root or "\\" in root or ".." in requested.parts:
            raise ValueError("root escapes volume mount")
        logical_path = requested if requested.is_absolute() else mount.joinpath(*requested.parts)
        try:
            logical_path.relative_to(mount)
        except ValueError as exc:
            raise ValueError("root escapes volume mount") from exc
        logical_root = str(logical_path)
        fetched_files = await gateway.list_files(
            identity.workspace_id,
            logical_root,
            max_depth=max_depth,
            max_files=max_files + 1,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid") from exc
    except Exception as exc:
        raise http_error(503, "volume_unavailable", "Workspace Volume is unavailable") from exc
    truncated = len(fetched_files) > max_files
    relative_paths: set[str] = set()
    for file in fetched_files:
        if not isinstance(file.path, str) or "\x00" in file.path or "\\" in file.path:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        path = PurePosixPath(file.path)
        if not path.is_absolute() or ".." in path.parts:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        try:
            relative = path.relative_to(logical_path)
            relative_to_mount = path.relative_to(mount)
        except ValueError as exc:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid") from exc
        if not relative.parts or not relative_to_mount.parts:
            raise http_error(400, "volume_tree_invalid", "Volume tree request is invalid")
        relative_paths.add(str(relative_to_mount))
    paths = sorted(relative_paths)[:max_files]
    directories: list[str] = []
    if logical_path == mount:
        directories = ["artifacts", "attachments", "files", "projects", "sessions"]
    return VolumeTreeResponse(
        paths=paths,
        directories=directories,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Artifacts Router (/api/artifacts)
# ---------------------------------------------------------------------------

artifacts_router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _to_artifact_response(ref: ArtifactRef) -> ArtifactResponse:
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


@artifacts_router.get(
    "/{artifact_id}",
    response_model=ArtifactResponse,
    operation_id="get_artifact",
    responses={
        404: {"description": "Artifact not found"},
        503: {"description": "Artifact storage is unavailable"},
    },
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
    return _to_artifact_response(ref)


@artifacts_router.get(
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
    capture(
        "artifact_downloaded",
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


# ---------------------------------------------------------------------------
# Attachments Router (/api/attachments)
# ---------------------------------------------------------------------------

attachments_router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@attachments_router.post(
    "",
    response_model=AttachmentResponse,
    status_code=201,
    operation_id="create_attachment",
    responses={
        400: {"description": "Attachment request is invalid"},
        503: {"description": "Attachment storage is unavailable"},
    },
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
    except AttachmentStorageError as exc:
        raise http_error(503, "attachment_unavailable", "Attachment storage is unavailable") from exc
    except AttachmentError as exc:
        raise http_error(400, "attachment_invalid", str(exc)) from exc
    except Exception as exc:
        raise http_error(503, "attachment_unavailable", "Attachment storage is unavailable") from exc
    return AttachmentResponse.model_validate(ref, from_attributes=True)


@attachments_router.get(
    "/{attachment_id}",
    response_model=AttachmentResponse,
    operation_id="get_attachment",
    responses={
        404: {"description": "Attachment not found"},
        503: {"description": "Attachment storage is unavailable"},
    },
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


router = APIRouter()
router.include_router(workspace_files_router)
router.include_router(volume_router)
router.include_router(artifacts_router)
router.include_router(attachments_router)

__all__ = [
    "_READ_ERRORS",
    "_WRITE_ERRORS",
    "_entry",
    "_raise_public_error",
    "artifacts_router",
    "attachments_router",
    "router",
    "volume_router",
    "workspace_files_router",
]
