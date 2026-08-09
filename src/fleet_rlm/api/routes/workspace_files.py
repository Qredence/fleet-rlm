"""Independent access to the LocalScope Workspace's public ``files/`` root."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from fleet_rlm.api.dependencies import LocalScopeDep, WorkspaceFileServiceDep
from fleet_rlm.api.errors import http_error
from fleet_rlm.api.schemas import (
    WorkspaceFileAppendRequest,
    WorkspaceFileDeleteRequest,
    WorkspaceFileDeleteResponse,
    WorkspaceFileEntryResponse,
    WorkspaceFileListResponse,
    WorkspaceFilePatchRequest,
    WorkspaceFileReadResponse,
    WorkspaceFileWriteRequest,
)
from fleet_rlm.files.workspace_access import (
    MAX_PUBLIC_LIST_LIMIT,
    MAX_PUBLIC_READ_CHARS,
    WorkspaceFileConflictError,
    WorkspaceFileEntry,
)

router = APIRouter(prefix="/api/files", tags=["workspace-files"])


def _entry(value: WorkspaceFileEntry) -> WorkspaceFileEntryResponse:
    return WorkspaceFileEntryResponse.model_validate(value, from_attributes=True)


def _raise_public_error(exc: BaseException) -> None:
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


@router.get("", response_model=WorkspaceFileListResponse, operation_id="list_workspace_files_api")
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


@router.get(
    "/stat",
    response_model=WorkspaceFileEntryResponse,
    operation_id="stat_workspace_file_api",
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
    if value is None:
        # stat() raises for missing files; the guard keeps type narrowing even
        # under `python -O`, where `assert` would be stripped.
        raise http_error(503, "workspace_files_unavailable", "Workspace files are unavailable")
    return _entry(value)


@router.get(
    "/content",
    response_model=WorkspaceFileReadResponse,
    operation_id="read_workspace_file_api",
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


@router.put(
    "/content",
    response_model=WorkspaceFileEntryResponse,
    operation_id="write_workspace_file_api",
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


@router.post(
    "/append",
    response_model=WorkspaceFileEntryResponse,
    operation_id="append_workspace_file_api",
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


@router.delete(
    "/content",
    response_model=WorkspaceFileDeleteResponse,
    operation_id="delete_workspace_file_api",
)
async def delete_workspace_file(
    body: WorkspaceFileDeleteRequest,
    identity: LocalScopeDep,
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileDeleteResponse:
    # Endpoint mirrors the delete Tool and keeps mutation semantics aligned
    # between the REST and RLM surfaces.
    try:
        await service.delete(
            identity.workspace_id,
            body.path,
            expected_sha256=body.expected_sha256,
        )
    except Exception as exc:
        _raise_public_error(exc)
    return WorkspaceFileDeleteResponse(path=body.path)


@router.patch(
    "/content",
    response_model=WorkspaceFileEntryResponse,
    operation_id="patch_workspace_file_api",
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
