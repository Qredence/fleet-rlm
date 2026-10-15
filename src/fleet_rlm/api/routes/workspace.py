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

from fleet_rlm.api.deps import Runtime, Settings
from fleet_rlm.api.errors import translate
from fleet_rlm.api.schemas import Attached, Deleted, Files, FileStat, FileWritten, WorkspaceWriteRequest

router = APIRouter(tags=["workspace"])
_MAX_FILE_BYTES = 25_000_000


@router.get("/sessions/{session_id}/files", response_model=Files)
@router.get("/sessions/{session_id}/workspace/tree", response_model=Files)
def list_files(session_id: str, runtime: Runtime) -> dict:
    with translate("Session workspace is unavailable"), runtime.turn_lock:
        runtime.harness.hydrate_volume_from_postgres(session_id)
        files = runtime.sandbox_mgr.list_workspace_files()
    return {"session_id": session_id, "files": files, "artifacts": [p for p in files if p.startswith("artifacts/")]}


@router.get("/sessions/{session_id}/workspace/stat/{rel_path:path}", response_model=FileStat)
def workspace_stat(session_id: str, rel_path: str, runtime: Runtime) -> dict:
    with translate("Workspace file is unavailable", invalid="Invalid workspace path", status=404), runtime.turn_lock:
        runtime.harness.hydrate_volume_from_postgres(session_id)
        content = runtime.sandbox_mgr.read_workspace_file(rel_path)
    return {"path": rel_path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


@router.get("/sessions/{session_id}/workspace/files/{rel_path:path}", response_class=FileResponse)
def read_workspace_file(session_id: str, rel_path: str, runtime: Runtime) -> FileResponse:
    descriptor, temporary = tempfile.mkstemp(prefix="fleet-download-")
    os.close(descriptor)
    try:
        unavailable = "Workspace file is unavailable"
        with translate(unavailable, invalid="Invalid workspace path", status=404), runtime.turn_lock:
            runtime.harness.hydrate_volume_from_postgres(session_id)
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


@router.put("/sessions/{session_id}/workspace/files/{rel_path:path}", response_model=FileWritten)
def write_workspace_file(session_id: str, rel_path: str, body: WorkspaceWriteRequest, runtime: Runtime) -> dict:
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
        with runtime.turn_lock:
            runtime.harness.hydrate_volume_from_postgres(session_id)
            mounted_path = runtime.sandbox_mgr.write_workspace_file(rel_path, content)
            runtime.harness.persist_workspace_files(session_id)
    return {
        "path": rel_path,
        "mounted_path": mounted_path,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


@router.delete("/sessions/{session_id}/workspace/files/{rel_path:path}", response_model=Deleted)
def delete_workspace_file(session_id: str, rel_path: str, runtime: Runtime) -> dict:
    unavailable = "Workspace file could not be deleted"
    with translate(unavailable, invalid="Invalid workspace path", status=404), runtime.turn_lock:
        runtime.harness.hydrate_volume_from_postgres(session_id)
        runtime.sandbox_mgr.delete_workspace_file(rel_path)
        runtime.harness.persist_workspace_files(session_id)
    return {"deleted": rel_path}


@router.post("/sessions/{session_id}/attach", response_model=Attached)
def attach_file(session_id: str, runtime: Runtime, settings: Settings, file: Annotated[UploadFile, File()]) -> dict:
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
                with runtime.turn_lock:
                    runtime.harness.hydrate_volume_from_postgres(session_id)
                    mounted_path = runtime.sandbox_mgr.write_workspace_file_from_path(relative_path, temporary)
                    runtime.harness.persist_workspace_files(session_id)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary)
        return {"mounted_path": mounted_path, "rel_path": relative_path}
    finally:
        file.file.close()
