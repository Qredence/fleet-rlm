"""POST /api/chat SSE endpoint for AI SDK UIMessage v1 streaming.

Mounted at app root (``/api/chat``) via ``main.py``, NOT inside the
``api_v1`` prefix. Uses FastAPI's native ``EventSourceResponse`` with
``x-vercel-ai-ui-message-stream: v1`` header.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from inspect import isawaitable
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from fleet_rlm.api.dependencies import (
    ConfigDepsDep,
    DiagnosticsDepsDep,
    HTTPIdentityDep,
    InterpreterPoolDepsDep,
    LmDepsDep,
    PersistenceDepsDep,
)
from fleet_rlm.api.events.project_sse import project_sse
from fleet_rlm.api.routers.ws.session import resolve_persisted_session_owner_proof
from fleet_rlm.api.runtime_services.chat_context import ChatExecutionContext, TurnControls
from fleet_rlm.api.runtime_services.chat_prepare_errors import public_prepare_error_detail
from fleet_rlm.api.runtime_services.chat_runtime import build_chat_agent_context, prepare_chat_runtime
from fleet_rlm.api.runtime_services.stream_turn import stream_turn
from fleet_rlm.api.schemas.chat import ChatMessage, ChatRequest
from fleet_rlm.files.attachment_resolution import AttachmentResolutionError, resolve_attachment_refs
from fleet_rlm.files.upload_staging import attachment_owner_scope
from fleet_rlm.integrations.daytona.volumes import DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventKind
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class _SSEPrepareError(Exception):
    """Captures error details from ``prepare_chat_runtime`` for SSE error raising.

    Raised inside ``_sse_send_error`` and caught by the handler to translate
    WS-oriented error callbacks into HTTP exceptions.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


async def _sse_send_error(*, code: str, message: str) -> bool:
    """Transport-neutral error callback: raise instead of writing to a WebSocket.

    Returns ``True`` to satisfy the callback signature, but actually raises so
    the caller's ``try/except`` can translate to an HTTP response.
    """
    raise _SSEPrepareError(code, message)


async def _sse_close_websocket(*, code: int = 1000) -> None:
    """Transport-neutral close callback: no-op for SSE."""
    del code


def _extract_latest_user_message(messages: list[ChatMessage]) -> str | None:
    """Scan backwards through *messages* for the last ``role: "user"`` message.

    Extracts text from ``content`` when present, or from ``parts`` (AI SDK
    UIMessage shape) when content is ``None``.  Returns ``None`` when no user
    message is found.
    """
    for msg in reversed(messages):
        if msg.role != "user":
            continue

        # Prefer the plain-text content field.
        if msg.content:
            return msg.content

        # Fall back to parts extraction (AI SDK UIMessage shape).
        if msg.parts:
            text_segments: list[str] = []
            for part in msg.parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        text_segments.append(text)
            if text_segments:
                return "".join(text_segments)

        return msg.content or ""

    return None


async def _ensure_turn_preamble(
    event_stream: AsyncIterator[RuntimeEvent],
) -> AsyncIterator[RuntimeEvent]:
    """Inject a synthetic ``TURN_STARTED`` preamble when the stream starts with
    a non-``TURN_STARTED`` event.

    This satisfies ``VAL-SSE-054`` (empty turn): when ``stream_turn`` yields
    only a terminal ``DONE``, the projector still emits ``start`` +
    ``start-step`` + ``finish-step`` + ``finish`` + ``[DONE]``.
    """
    started_seen = False
    async for event in event_stream:
        if not started_seen:
            started_seen = True
            if event.kind != RuntimeEventKind.TURN_STARTED:
                yield RuntimeEvent(
                    kind=RuntimeEventKind.TURN_STARTED,
                    text="started",
                    payload={"message_id": str(uuid4())},
                )
        yield event


async def _build_and_stream(
    ctx: ChatExecutionContext,
    agent_runtime: object,
    user_message: str,
    request: Request,
    cancel_flag: dict[str, bool],
) -> AsyncIterator[ServerSentEvent]:
    """Run ``stream_turn`` + ``project_sse`` with cancellation awareness.

    Yields SSE events. Catches exceptions after headers have been
    committed (HTTP 200 sent) and emits ``error`` + ``[DONE]`` to produce a
    well-formed terminal.
    """
    try:
        event_iter = _ensure_turn_preamble(_runtime_event_stream(ctx, agent_runtime, user_message))
        async for line in project_sse(event_iter, cancel_flag=cancel_flag):
            if await request.is_disconnected():
                cancel_flag["cancelled"] = True
            yield _server_sent_event_from_data_line(line)
    except _SSEPrepareError:
        # Raised by _sse_send_error — caught earlier; this path is defensive.
        raise
    except Exception as exc:
        # Error after headers already committed — emit error + [DONE].
        logger.debug("Error during SSE streaming: %s", exc, exc_info=True)
        yield ServerSentEvent(raw_data=json.dumps({"type": "error", "text": "Stream failed"}))
        yield ServerSentEvent(raw_data="[DONE]")


async def _runtime_event_stream(
    ctx: ChatExecutionContext,
    agent_runtime: object,
    user_message: str,
) -> AsyncIterator[RuntimeEvent]:
    """Normalize ``stream_turn`` outputs to an async iterator of runtime events."""
    event_stream: Any = stream_turn(ctx=ctx, agent_runtime=agent_runtime, message=user_message)
    if isawaitable(event_stream):
        event_stream = await event_stream
    async for event in event_stream:
        yield event


def _server_sent_event_from_data_line(line: str) -> ServerSentEvent:
    """Convert an existing ``data:`` wire line into FastAPI's SSE event type."""
    stripped = line.rstrip("\n")
    if stripped.startswith("data: "):
        return ServerSentEvent(raw_data=stripped.removeprefix("data: "))
    return ServerSentEvent(raw_data=stripped)


def _status_for_prepare_code(code: str) -> int:
    """Map ``prepare_chat_runtime`` error codes to HTTP status codes."""
    mapping: dict[str, int] = {
        "planner_initialization_failed": 500,
        "planner_missing": 500,
        "tenant_forbidden": 403,
        "auth_failed": 401,
        "durable_state_unavailable": 503,
    }
    return mapping.get(code, 500)


@dataclass(slots=True)
class _PreparedChatStream:
    events: AsyncIterator[ServerSentEvent]


async def _prepare_chat_event_stream(
    request: Request,
    response: Response,
    body: ChatRequest,
    identity: HTTPIdentityDep,
    config_deps: ConfigDepsDep,
    lm_deps: LmDepsDep,
    persistence_deps: PersistenceDepsDep,
    diagnostics_deps: DiagnosticsDepsDep,
    interpreter_pool_deps: InterpreterPoolDepsDep,
) -> _PreparedChatStream:
    """Resolve all pre-stream dependencies before FastAPI opens the SSE response."""
    # ── 1. Extract the latest user message ────────────────────────────
    user_message = _extract_latest_user_message(body.messages)
    if user_message is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No user message found in messages. At least one message with role='user' is required.",
        )

    # ── 2. Prepare the chat runtime ───────────────────────────────────
    # Use SSE-appropriate error/close callbacks that raise HTTP exceptions
    # instead of writing to a WebSocket.
    try:
        runtime = await prepare_chat_runtime(
            config_deps=config_deps,
            lm_deps=lm_deps,
            persistence_deps=persistence_deps,
            diagnostics_deps=diagnostics_deps,
            identity=identity,
            send_error=_sse_send_error,
            close_websocket=_sse_close_websocket,
        )
    except _SSEPrepareError as e:
        status_code = _status_for_prepare_code(e.code)
        raise HTTPException(
            status_code=status_code,
            detail=public_prepare_error_detail(code=e.code, message=e.message),
        )
    except Exception as exc:
        # Unexpected (non-_SSEPrepareError) prepare failure: log full details
        # server-side at ERROR level (visible under default logging config)
        # while the client only ever sees the sanitized detail below.
        logger.exception("Error preparing SSE runtime: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=public_prepare_error_detail(),
        ) from exc

    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=public_prepare_error_detail(),
        )

    # ── 2b. Resolve attachment refs before streaming ──────────────────
    attached_files = None
    if body.attachment_refs:
        owner_scope = attachment_owner_scope(
            tenant_claim=identity.tenant_claim,
            user_claim=identity.user_claim,
        )
        persisted_session_owner_proof = await resolve_persisted_session_owner_proof(
            persistence=runtime.persistence,
            identity_rows=runtime.identity_rows,
            session_id=body.session_id or "",
            owner_tenant_claim=identity.tenant_claim,
            owner_user_claim=identity.user_claim,
        )
        try:
            attached_files = resolve_attachment_refs(
                volume_mount_path=str(DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH),
                session_id=body.session_id,
                attachment_ids=body.attachment_refs,
                owner_scope=owner_scope,
                persisted_session_owner_proof=persisted_session_owner_proof,
            )
        except AttachmentResolutionError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # ── 3. Build ChatExecutionContext ─────────────────────────────────
    cfg = config_deps.config
    cancel_flag: dict[str, bool] = {"cancelled": False}

    try:
        agent_context = await build_chat_agent_context(runtime, pool=interpreter_pool_deps.pool)
    except Exception as exc:
        # Same rationale as above: build_chat_agent_context() failures (e.g.
        # Daytona/interpreter-pool acquisition) must stay visible server-side.
        logger.exception("Error building agent context: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=public_prepare_error_detail(),
        ) from exc

    async def _event_stream() -> AsyncIterator[ServerSentEvent]:
        async with agent_context as agent:
            if body.session_id:
                agent._db_session_id = body.session_id
            ctx = ChatExecutionContext(
                prepared=runtime,
                identity=identity,
                session_id=body.session_id,
                canonical_workspace_id=_sanitize_id(
                    identity.tenant_claim,
                    cfg.ws_default_workspace_id,
                ),
                canonical_user_id=_sanitize_id(
                    identity.user_claim,
                    cfg.ws_default_user_id,
                ),
                owner_tenant_claim=identity.tenant_claim,
                owner_user_claim=identity.user_claim,
                cancel_flag=cancel_flag,
                controls=TurnControls(
                    execution_mode=body.execution_mode,
                    repo_url=body.repo_url,
                    repo_ref=body.repo_ref,
                    context_paths=body.context_paths or [],
                    batch_concurrency=body.batch_concurrency,
                    docs_path=body.docs_path,
                    trace=body.trace,
                    trace_mode=body.trace_mode,
                    selected_skill_ids=body.selected_skill_ids or [],
                    attached_files=attached_files,
                ),
            )
            async for event in _build_and_stream(ctx, agent, user_message, request, cancel_flag):
                yield event

    response.headers["x-vercel-ai-ui-message-stream"] = "v1"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["x-accel-buffering"] = "no"
    return _PreparedChatStream(events=_event_stream())


ChatEventStreamDep = Annotated[_PreparedChatStream, Depends(_prepare_chat_event_stream)]


@router.post(
    "",
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "SSE streaming response. Returns a Server-Sent Events stream with "
            "Content-Type: text/event-stream and x-vercel-ai-ui-message-stream: v1 header.",
            "content": {"text/event-stream": {}},
        },
    },
)
async def chat_completion(prepared_stream: ChatEventStreamDep) -> AsyncIterator[ServerSentEvent]:
    """Handle ``POST /api/chat`` SSE streaming chat completion.

    Authenticates via ``require_http_identity`` (HTTPBearer → NormalizedIdentity),
    builds a ``ChatExecutionContext`` from the request and identity, calls
    ``stream_turn()``, and projects via ``project_sse()`` over an
    ``EventSourceResponse``.

    Cancellation is driven by ``request.is_disconnected()`` flipping
    ``cancel_flag["cancelled"]``; the runtime's ``cancel_check`` polls the
    same flag.
    """
    async for event in prepared_stream.events:
        yield event
