"""A Session's workspace files: list, stat, read, write, delete, and attach uploads."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from fleet_rlm.api.deps import LOCKED, Runtime, Settings, session_lock, translate
from fleet_rlm.api.schemas import Attached, Deleted, Files, FileStat, FileWritten, WorkspaceWriteRequest

router = APIRouter(tags=["workspace"])
_MAX_FILE_BYTES = 25_000_000


@router.get("/sessions/{session_id}/files", response_model=Files, responses=LOCKED)
@router.get("/sessions/{session_id}/workspace/tree", response_model=Files, responses=LOCKED)
def list_files(session_id: str, runtime: Runtime) -> dict:
    """List a Session's workspace files and the artifacts among them."""
    with translate("Session workspace is unavailable"), session_lock(runtime):
        files = runtime.sandbox_mgr.list_workspace_files()
    return {"session_id": session_id, "files": files, "artifacts": [p for p in files if p.startswith("artifacts/")]}


@router.get("/sessions/{session_id}/workspace/stat/{rel_path:path}", response_model=FileStat, responses=LOCKED)
def workspace_stat(rel_path: str, runtime: Runtime) -> dict:
    """Return the size and SHA-256 digest of one workspace file."""
    with (
        translate("Workspace file is unavailable", invalid="Invalid workspace path", status=404),
        session_lock(runtime),
    ):
        content = runtime.sandbox_mgr.read_workspace_file(rel_path)
    return {"path": rel_path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


@router.get("/sessions/{session_id}/workspace/files/{rel_path:path}", response_class=FileResponse, responses=LOCKED)
def read_workspace_file(rel_path: str, runtime: Runtime) -> FileResponse:
    """Return one workspace file as a download, capped at 25 MB.

    An unavailable or invalid path returns 404.
    """
    descriptor, temporary = tempfile.mkstemp(prefix="fleet-download-")
    os.close(descriptor)
    try:
        unavailable = "Workspace file is unavailable"
        with translate(unavailable, invalid="Invalid workspace path", status=404), session_lock(runtime):
            runtime.sandbox_mgr.read_workspace_file_to_path(rel_path, temporary, max_bytes=_MAX_FILE_BYTES)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return FileResponse(
        temporary,
        media_type=mimetypes.guess_type(rel_path)[0] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{Path(rel_path).name}"'},
        background=BackgroundTask(os.unlink, temporary),
    )


@router.put("/sessions/{session_id}/workspace/files/{rel_path:path}", response_model=FileWritten, responses=LOCKED)
def write_workspace_file(rel_path: str, body: WorkspaceWriteRequest, runtime: Runtime) -> dict:
    """Write one workspace file from text or base64 content, capped at 25 MB.

    Supplying both or neither of the content fields returns 422.
    """
    with translate("Workspace write failed", invalid="Invalid workspace path or content"):
        if (body.content is None) == (body.content_base64 is None):
            raise HTTPException(status_code=422, detail="Supply exactly one of content or content_base64")
        content = (
            body.content.encode("utf-8")
            if body.content is not None
            else base64.b64decode(body.content_base64 or "", validate=True)
        )
        if len(content) > _MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Workspace file exceeds the 25 MB limit")
        with session_lock(runtime):
            mounted_path = runtime.sandbox_mgr.write_workspace_file(rel_path, content)
    return {
        "path": rel_path,
        "mounted_path": mounted_path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


@router.delete("/sessions/{session_id}/workspace/files/{rel_path:path}", response_model=Deleted, responses=LOCKED)
def delete_workspace_file(rel_path: str, runtime: Runtime) -> dict:
    """Delete one workspace file.

    Failures return 404.
    """
    unavailable = "Workspace file could not be deleted"
    with translate(unavailable, invalid="Invalid workspace path", status=404), session_lock(runtime):
        runtime.sandbox_mgr.delete_workspace_file(rel_path)
    return {"deleted": rel_path}


@router.post("/sessions/{session_id}/attach", response_model=Attached, responses=LOCKED)
def attach_file(runtime: Runtime, settings: Settings, file: Annotated[UploadFile, File()]) -> dict:
    """Store an uploaded attachment under files/ in the workspace.

    Uploads stream to disk and return 413 above the size limit.
    """
    try:
        with translate("Attachment storage is unavailable"):
            name = (file.filename or "attachment").replace("\\", "/").split("/")[-1]
            if not name or name in {".", ".."} or "\x00" in name:
                raise HTTPException(status_code=422, detail="Invalid attachment filename")
            limit = min(settings.max_upload_bytes, _MAX_FILE_BYTES)
            descriptor, temporary = tempfile.mkstemp(prefix="fleet-upload-")
            try:
                size = 0
                with os.fdopen(descriptor, "wb") as output:
                    while chunk := file.file.read(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(status_code=413, detail="Attachment exceeds the configured size limit")
                        output.write(chunk)
                relative_path = f"files/{name}"
                with session_lock(runtime):
                    mounted_path = runtime.sandbox_mgr.write_workspace_file_from_path(relative_path, temporary)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary)
        return {"mounted_path": mounted_path, "rel_path": relative_path}
    finally:
        file.file.close()
