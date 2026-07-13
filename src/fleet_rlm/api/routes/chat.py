"""POST /api/chat — typed SSE RuntimeEvents (no DSPy/Daytona in this module)."""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api.dependencies import (
    AttachmentStoreDep,
    OptionalSessionRepositoryDep,
    TurnCoordinatorDep,
)
from fleet_rlm.api.identity import RequestIdentity, get_request_identity
from fleet_rlm.api.schemas import ChatRequest
from fleet_rlm.api.sse import AI_SDK_UI_STREAM_HEADERS, AISDKUIProjector, SSEProjector, _json_default
from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.files.errors import AttachmentNotFoundError
from fleet_rlm.sessions.errors import SessionNotFoundError

router = APIRouter(tags=["chat"])


async def validate_chat_attachments(
    body: ChatRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    attachment_store: AttachmentStoreDep,
) -> None:
    """Dependency: reject invalid attachment_ids with HTTP 400 before SSE starts."""
    if not body.attachment_ids:
        return
    try:
        for attachment_id in body.attachment_ids:
            result = attachment_store.get(
                attachment_id,
                user_id=identity.user_id,
                workspace_id=identity.workspace_id,
            )
            if inspect.isawaitable(result):
                await result
    except AttachmentNotFoundError as exc:
        raise HTTPException(
            status_code=400,
            detail="invalid attachment reference",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="attachment storage unavailable") from exc


async def validate_chat_session(
    body: ChatRequest,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    repository: OptionalSessionRepositoryDep,
) -> None:
    """Reject missing or foreign Sessions before EventSourceResponse starts."""
    if body.session_id is None or repository is None:
        return
    try:
        snapshot = await repository.load(body.session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    session = snapshot.session
    if session.user_id != identity.user_id or session.workspace_id != identity.workspace_id:
        raise HTTPException(status_code=404, detail="session not found")


def set_ai_sdk_ui_stream_headers(response: Response) -> None:
    """Set protocol headers before FastAPI constructs the SSE response."""
    # FastAPI's native SSE response supplies Cache-Control and
    # X-Accel-Buffering. Adding them here would duplicate the latter.
    response.headers["connection"] = AI_SDK_UI_STREAM_HEADERS["connection"]
    response.headers["x-vercel-ai-ui-message-stream"] = AI_SDK_UI_STREAM_HEADERS["x-vercel-ai-ui-message-stream"]


@router.post(
    "/api/chat",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "AI SDK UI 7 v1 UIMessage SSE stream",
            "content": {"text/event-stream": {}},
            "headers": {
                "x-vercel-ai-ui-message-stream": {
                    "description": "AI SDK UI message stream protocol version",
                    "schema": {"type": "string", "const": "v1"},
                }
            },
        },
        400: {"description": "Invalid attachment reference"},
        404: {"description": "Session not found"},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    coordinator: TurnCoordinatorDep,
    ___: Annotated[None, Depends(set_ai_sdk_ui_stream_headers)],
    __: Annotated[None, Depends(validate_chat_session)],
    _: Annotated[None, Depends(validate_chat_attachments)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Stream one chat Turn as an AI SDK UI 7 message stream."""
    from fleet_rlm.rlm.cancel import get_run_cancel_registry

    command = ChatTurnCommand(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        session_id=body.session_id or uuid4(),
        message=body.message,
        attachment_ids=tuple(body.attachment_ids),
        idempotency_key=(idempotency_key or "").strip(),
    )

    stream: Any = coordinator.stream(command)
    projector = AISDKUIProjector()
    run_id: UUID | None = None
    try:
        async for event in stream:
            run_id = event.run_id
            if await request.is_disconnected():
                get_run_cancel_registry().request_cancel(event.run_id)
                break
            for chunk in projector.project(event):
                yield ServerSentEvent(raw_data=json.dumps(chunk, default=_json_default))
        if not await request.is_disconnected():
            yield ServerSentEvent(raw_data="[DONE]")
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    finally:
        if run_id is not None and await request.is_disconnected():
            get_run_cancel_registry().request_cancel(run_id)
        await stream.aclose()


# Re-export projector for tests that assert route composition stays thin.
__all__ = ["router", "SSEProjector"]
