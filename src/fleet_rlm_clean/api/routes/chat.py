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


def get_turn_coordinator(request: Request) -> TurnCoordinator:
    """Resolve coordinator from app.state (tests inject fakes here)."""
    coordinator = getattr(request.app.state, "turn_coordinator", None)
    if coordinator is None:
        coordinator = TurnCoordinator()
        request.app.state.turn_coordinator = coordinator
    return coordinator


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
        }
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    identity: Annotated[RequestIdentity, Depends(get_request_identity)],
    coordinator: Annotated[TurnCoordinator, Depends(get_turn_coordinator)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream one chat turn as typed SSE. Route performs no DSPy/Daytona SDK calls."""
    command = ChatTurnCommand(
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
        session_id=body.session_id or uuid4(),
        message=body.message,
        attachment_ids=tuple(body.attachment_ids),
    )
    stream = coordinator.stream(command)
    try:
        async for event in stream:
            if await request.is_disconnected():
                break
            yield runtime_event_to_sse(event)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    finally:
        await stream.aclose()


# Re-export projector for tests that assert route composition stays thin.
__all__ = ["router", "runtime_event_to_sse", "get_turn_coordinator", "SSEProjector"]
