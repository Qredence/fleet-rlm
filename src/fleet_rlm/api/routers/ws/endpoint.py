"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.observability.trace_context import (
    runtime_distinct_id_context,
)
from fleet_rlm.runtime.config import build_dspy_context

from ...dependencies import get_server_state_from_websocket
from ...execution import (
    ExecutionSubscription,
)
from ...server_utils import sanitize_id as _sanitize_id
from .execution_support import get_execution_emitter
from .failures import chat_startup_error_payload
from .helpers import (
    _authenticate_websocket,
    _close_websocket_safely,
    _error_envelope,
    _sanitize_for_log,
    _try_send_json,
)
from .messages import parse_ws_message_or_send_error
from .persistence import persist_session_state
from .runtime import (
    _build_chat_agent_context,
    _new_chat_session_state,
    _prepare_chat_runtime,
    _set_interpreter_default_profile,
)
from .stream import _chat_message_loop

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


@router.websocket("/ws/execution")
async def execution_stream(
    websocket: WebSocket,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Dedicated execution stream for Artifact Canvas consumers."""
    state = get_server_state_from_websocket(websocket)
    identity = await _authenticate_websocket(websocket, state)
    if identity is None:
        return

    subscription = ExecutionSubscription(
        workspace_id=_sanitize_id(identity.tenant_claim, "default"),
        user_id=_sanitize_id(identity.user_claim, "anonymous"),
        session_id=str(session_id or "").strip(),
    )
    if not subscription.session_id:
        await websocket.accept()
        if await _try_send_json(
            websocket,
            _error_envelope(
                code="missing_session_id",
                message="Missing required query param: session_id",
            ),
        ):
            await _close_websocket_safely(websocket, code=1008)
        return
    emitter = get_execution_emitter(state)
    await emitter.connect(websocket, subscription)

    try:
        while True:
            # Keep the socket alive for heartbeat/ping frames.
            await websocket.receive()
    except WebSocketDisconnect:
        await emitter.disconnect(websocket)
    except Exception:
        await emitter.disconnect(websocket)


@router.websocket("/ws/chat")
async def chat_streaming(websocket: WebSocket) -> None:
    """Streaming WebSocket endpoint with native DSPy async streaming."""
    state = get_server_state_from_websocket(websocket)
    identity = await _authenticate_websocket(websocket, state)
    if identity is None:
        return

    await websocket.accept()
    runtime = await _prepare_chat_runtime(
        websocket=websocket,
        state=state,
        identity=identity,
    )
    if runtime is None:
        return

    analytics_distinct_id = (identity.user_claim or "").strip() or None
    try:
        with (
            runtime_distinct_id_context(analytics_distinct_id),
            build_dspy_context(lm=runtime.planner_lm),
        ):
            initial_msg = None
            while initial_msg is None:
                raw_payload = await websocket.receive_json()
                initial_msg = await parse_ws_message_or_send_error(
                    websocket=websocket,
                    raw_payload=raw_payload,
                )

            agent_context = _build_chat_agent_context(
                runtime,
                runtime_mode=getattr(initial_msg, "runtime_mode", "modal_chat"),
            )
            async with agent_context as agent:
                interpreter = getattr(agent, "interpreter", None)
                _set_interpreter_default_profile(interpreter, runtime.cfg)
                session = _new_chat_session_state(runtime, identity)

                async def local_persist(
                    *, include_volume_save: bool = True, latest_user_message: str = ""
                ) -> None:
                    await persist_session_state(
                        state=state,
                        agent=agent,
                        session_record=session.session_record,
                        active_manifest_path=session.active_manifest_path,
                        active_run_db_id=session.active_run_db_id,
                        interpreter=interpreter,
                        repository=runtime.repository,
                        identity_rows=runtime.identity_rows,
                        persistence_required=runtime.persistence_required,
                        include_volume_save=include_volume_save,
                        latest_user_message=latest_user_message,
                    )

                await _chat_message_loop(
                    websocket=websocket,
                    state=state,
                    runtime=runtime,
                    agent=agent,
                    interpreter=interpreter,
                    session=session,
                    local_persist=local_persist,
                    initial_message=initial_msg,
                )
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.exception("WebSocket chat startup failed: %s", _sanitize_for_log(exc))
        if await _try_send_json(websocket, chat_startup_error_payload(exc)):
            await _close_websocket_safely(websocket, code=1011)
