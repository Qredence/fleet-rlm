"""Session chat, immutable file publication, and non-owning observer streams."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.concurrency import run_in_threadpool

from fleet_rlm.api.deps import Settings, StoreDep, translate
from fleet_rlm.api.schemas import (
    MessageActivityRequest,
    MessagePage,
    PublishedFile,
    PublishedFiles,
    WorkspaceInventory,
)
from fleet_rlm.object_storage import ObjectStorage
from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES, validate_session_id

router = APIRouter(prefix="/sessions/{session_id}", tags=["chat"])


def _trace_links(rows: list[dict[str, Any]], request: Request, settings: Any) -> list[dict[str, Any]]:
    descriptor = settings.mlflow_descriptor()
    base = descriptor.get("workspace_host") if settings.mlflow_mode == "databricks" else descriptor.get("tracking_uri")
    experiment = getattr(request.app.state, "mlflow_experiment_id", None)
    if base and str(base).startswith(("http://", "https://")) and experiment:
        prefix = str(base).rstrip("/") + ("/ml" if settings.mlflow_mode == "databricks" else "/#")
        for row in rows:
            if row.get("trace_id"):
                row["trace_url"] = (
                    f"{prefix}/experiments/{quote(str(experiment), safe='')}/traces"
                    f"?selectedTraceId={quote(row['trace_id'], safe='')}"
                )
    return rows


@router.get("/messages", response_model=MessagePage)
def messages(
    session_id: str,
    store: StoreDep,
    request: Request,
    settings: Settings,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, Any]:
    """Read a chronological page of durable conversation entries."""
    with translate("Messages are unavailable"):
        rows = store.messages(validate_session_id(session_id), before=before, limit=limit)
    return {"messages": _trace_links(rows, request, settings), "next_before": rows[0]["seq"] if rows else None}


@router.post("/messages/{message_id}/activity", status_code=204)
def save_message_activity(session_id: str, message_id: str, body: MessageActivityRequest, store: StoreDep) -> Response:
    """Persist bounded streamed activity on its completed assistant message."""
    with translate("Message activity could not be saved", invalid="Invalid Session ID"):
        session_id = validate_session_id(session_id)
        message_id = validate_session_id(message_id)
        saved = store.save_message_activity(
            session_id,
            message_id,
            [part.model_dump() for part in body.activity],
        )
        if not saved:
            raise HTTPException(404, "Completed assistant message was not found")
    return Response(status_code=204)


@router.get("/published-files", response_model=PublishedFiles)
def published_files(session_id: str, store: StoreDep) -> dict[str, Any]:
    """List immutable files for the selected session."""
    with translate("Published files are unavailable"):
        return {"files": store.published_files(validate_session_id(session_id))}


@router.get("/published-files/{file_id}")
def download_file(session_id: str, file_id: str, store: StoreDep, settings: Settings) -> RedirectResponse:
    """Authorize a session file and issue a short-lived download redirect."""
    with translate("Published file is unavailable"):
        file = store.published_file(validate_session_id(session_id), validate_session_id(file_id))
        if file is None:
            raise HTTPException(404, "Published file was not found")
        return RedirectResponse(ObjectStorage(settings).download_url(file), status_code=307)


@router.post("/attachments", response_model=PublishedFile)
def upload(session_id: str, store: StoreDep, settings: Settings, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Publish a bounded upload before saving its session-owned metadata."""
    with translate("Attachment publication failed"):
        validate_session_id(session_id)
        name = (file.filename or "attachment").replace("\\", "/").split("/")[-1]
        if name in {"", ".", ".."} or "\x00" in name:
            raise ValueError("Invalid filename")
        descriptor, temporary = tempfile.mkstemp(prefix="fleet-publish-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                size = 0
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > min(settings.max_upload_bytes, MAX_WORKSPACE_FILE_BYTES):
                        raise HTTPException(413, "Attachment exceeds the configured limit")
                    stream.write(chunk)
            record = ObjectStorage(settings).upload(session_id, Path(temporary), filename=name, origin="upload")
            store.save_upload(session_id, record)
            return {key: value for key, value in record.items() if key not in {"bucket", "object_key"}}
        finally:
            Path(temporary).unlink(missing_ok=True)
            file.file.close()


@router.get("/workspace/index", response_model=WorkspaceInventory)
def workspace_index(session_id: str, store: StoreDep) -> dict[str, Any]:
    """Read the last known workspace inventory without acquiring a runtime."""
    with translate("Workspace inventory is unavailable"):
        return store.workspace_inventory(validate_session_id(session_id))


@router.get("/updates", response_class=EventSourceResponse)
async def updates(
    session_id: str, request: Request, store: StoreDep, settings: Settings
) -> AsyncIterator[ServerSentEvent]:
    """Observe saved state only. Disconnect has no ownership or cancellation effects."""
    with translate("Session is unavailable", invalid="Invalid Session ID"):
        validate_session_id(session_id)

    def snapshot() -> dict[str, Any]:
        return dict(
            messages=_trace_links(store.messages(session_id), request, settings),
            files=store.published_files(session_id),
            workspace=store.workspace_inventory(session_id),
            settings=store.runtime_settings(session_id),
        )

    previous = ""
    while not await request.is_disconnected():
        state = await run_in_threadpool(snapshot)
        encoded = json.dumps(state, default=str, sort_keys=True)
        if encoded != previous:
            yield ServerSentEvent(event="state", raw_data=encoded)
            previous = encoded
        else:
            yield ServerSentEvent(comment="keepalive")
        await asyncio.sleep(2)
