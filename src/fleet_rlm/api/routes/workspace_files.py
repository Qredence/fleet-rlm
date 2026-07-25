"""Independent access to the LocalScope Workspace's public ``files/`` root."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from fleet_rlm.api.dependencies import WorkspaceFileServiceDep
from fleet_rlm.api.local_scope import LocalScope, get_local_scope
from fleet_rlm.api.schemas import (
    WorkspaceFileAppendRequest,
    WorkspaceFileEntryResponse,
    WorkspaceFileListResponse,
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
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_file_conflict",
                "message": "Workspace file precondition did not match",
            },
        ) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={"code": "workspace_file_not_found", "message": "Workspace file not found"},
        ) from exc
    if isinstance(exc, (ValueError, IsADirectoryError, NotADirectoryError)):
        raise HTTPException(
            status_code=400,
            detail={"code": "workspace_file_invalid", "message": "Workspace file request is invalid"},
        ) from exc
    raise HTTPException(
        status_code=503,
        detail={"code": "workspace_files_unavailable", "message": "Workspace files are unavailable"},
    ) from exc


@router.get("", response_model=WorkspaceFileListResponse, operation_id="list_workspace_files_api")
async def list_workspace_files(
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    service: WorkspaceFileServiceDep,
    path: str = ".",
    limit: int = Query(default=MAX_PUBLIC_LIST_LIMIT, ge=1, le=MAX_PUBLIC_LIST_LIMIT),
    after: str | None = None,
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
    path: str,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    service: WorkspaceFileServiceDep,
) -> WorkspaceFileEntryResponse:
    try:
        value = await service.stat(identity.workspace_id, path)
        if value is None:
            raise FileNotFoundError(path)
    except Exception as exc:
        _raise_public_error(exc)
    assert value is not None
    return _entry(value)


@router.get(
    "/content",
    response_model=WorkspaceFileReadResponse,
    operation_id="read_workspace_file_api",
)
async def read_workspace_file(
    path: str,
    identity: Annotated[LocalScope, Depends(get_local_scope)],
    service: WorkspaceFileServiceDep,
    cursor: str | None = None,
    max_chars: int = Query(default=MAX_PUBLIC_READ_CHARS, ge=1, le=MAX_PUBLIC_READ_CHARS),
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
    identity: Annotated[LocalScope, Depends(get_local_scope)],
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
    identity: Annotated[LocalScope, Depends(get_local_scope)],
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
