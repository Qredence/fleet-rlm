"""POST /api/chat — typed SSE RuntimeEvents (no DSPy/Daytona in this module)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm_clean.api.identity import RequestIdentity, get_request_identity
from fleet_rlm_clean.api.schemas import ChatRequest
from fleet_rlm_clean.api.sse import SSEProjector, _event_to_public_dict
from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator
from fleet_rlm_clean.rlm.events import RuntimeEvent
from fleet_rlm_clean.sessions.errors import SessionNotFoundError

router = APIRouter(tags=["chat"])


def _default_file_stores(request: Request) -> tuple[Any, Any]:
    """Lazy host stores matching files/artifacts route defaults."""
    from pathlib import Path

    from fleet_rlm_clean.artifacts.store import LocalArtifactStore
    from fleet_rlm_clean.config import Settings
    from fleet_rlm_clean.files.uploads import LocalAttachmentStore

    settings = getattr(request.app.state, "settings", None) or Settings()
    attachment_store = getattr(request.app.state, "attachment_store", None)
    if attachment_store is None:
        root = settings.upload_root or str(Path.cwd() / ".fleet_clean_uploads")
        attachment_store = LocalAttachmentStore(root, max_bytes=settings.max_upload_bytes)
        request.app.state.attachment_store = attachment_store
    artifact_store = getattr(request.app.state, "artifact_store", None)
    if artifact_store is None:
        if settings.artifact_root:
            art_root = settings.artifact_root
        elif settings.upload_root:
            art_root = str(Path(settings.upload_root).parent / "artifacts")
        else:
            art_root = str(Path.cwd() / ".fleet_clean_artifacts")
        artifact_store = LocalArtifactStore(art_root, max_bytes=settings.max_artifact_bytes)
        request.app.state.artifact_store = artifact_store
    return attachment_store, artifact_store


def get_turn_coordinator(request: Request) -> TurnCoordinator:
    """Resolve coordinator from app.state (tests inject fakes here).

    Default coordinator wraps OfflineContextBuilder with capability assembly so
    Skill/File tools bind when registry and stores are present.
    """
    coordinator = getattr(request.app.state, "turn_coordinator", None)
    if coordinator is not None:
        return coordinator

    from fleet_rlm_clean.chat.capabilities import CapabilityContextBuilder
    from fleet_rlm_clean.chat.context_builder import OfflineContextBuilder

    registry = getattr(request.app.state, "skill_registry", None)
    attachment_store, artifact_store = _default_file_stores(request)
    builder = CapabilityContextBuilder(
        OfflineContextBuilder(),
        skill_registry=registry,
        attachment_store=attachment_store,
        artifact_store=artifact_store,
    )
    coordinator = TurnCoordinator(context_builder=builder)
    request.app.state.turn_coordinator = coordinator
    return coordinator


def validate_chat_attachments(
    body: ChatRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
) -> None:
    """Dependency: reject invalid attachment_ids with HTTP 400 before SSE starts."""
    from fleet_rlm_clean.chat.capabilities import (
        AttachmentValidationError,
        validate_attachment_ids,
    )

    if not body.attachment_ids:
        return
    attachment_store, _artifact_store = _default_file_stores(request)
    try:
        validate_attachment_ids(
            attachment_store,
            tuple(body.attachment_ids),
            user_id=identity.user_id,
            workspace_id=identity.workspace_id,
        )
    except AttachmentValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="invalid attachment reference",
        ) from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    raise TypeError(msg)


def runtime_event_to_sse(event: RuntimeEvent) -> ServerSentEvent:
    """Project one RuntimeEvent into a FastAPI ServerSentEvent."""
    payload = _event_to_public_dict(event)
    return ServerSentEvent(
        raw_data=json.dumps(payload, default=_json_default),
        id=str(event.sequence),
    )


@router.post(
    "/api/chat",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE stream of RuntimeEvent v1 envelopes",
            "content": {"text/event-stream": {}},
        },
        400: {"description": "Invalid attachment reference"},
        404: {"description": "Session not found"},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    coordinator: Annotated[TurnCoordinator, Depends(get_turn_coordinator)],
    _: Annotated[None, Depends(validate_chat_attachments)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream one chat turn as typed SSE. Route performs no DSPy/Daytona SDK calls."""
    from fleet_rlm_clean.rlm.cancel import get_run_cancel_registry

    command = ChatTurnCommand(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        session_id=body.session_id or uuid4(),
        message=body.message,
        attachment_ids=tuple(body.attachment_ids),
    )

    stream = coordinator.stream(command)
    run_id: UUID | None = None
    try:
        async for event in stream:
            run_id = event.run_id
            if await request.is_disconnected():
                get_run_cancel_registry().request_cancel(event.run_id)
                break
            yield runtime_event_to_sse(event)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    finally:
        if run_id is not None and await request.is_disconnected():
            get_run_cancel_registry().request_cancel(run_id)
        await stream.aclose()


# Re-export projector for tests that assert route composition stays thin.
__all__ = ["router", "runtime_event_to_sse", "get_turn_coordinator", "SSEProjector"]
