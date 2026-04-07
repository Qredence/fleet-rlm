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
from ...events import ExecutionSubscription
from ...runtime_services.chat_persistence import (
    build_local_persist_fn as _build_local_persist_fn,
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
from .runtime import (
    _build_chat_agent_context,
    _new_chat_session_state,
    _prepare_chat_runtime,
    _set_interpreter_default_profile,
)
from .stream import _chat_message_loop

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


async def _reject_unsupported_identity_query_params(
    websocket: WebSocket,
    *,
    workspace_id: str | None,
    user_id: str | None,
) -> bool:
    if workspace_id is None and user_id is None:
        return False

    await websocket.accept()
    if await _try_send_json(
        websocket,
        _error_envelope(
            code="unsupported_identity_query_params",
            message=(
                "Execution stream identity is derived from auth. Remove "
                "workspace_id/user_id query params and use session_id only."
            ),
        ),
    ):
        await _close_websocket_safely(websocket, code=1008)
    return True


async def _run_chat_execution_stream(
    *,
    websocket: WebSocket,
    state,
    identity,
) -> None:
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

            agent_context = _build_chat_agent_context(runtime)
            async with agent_context as agent:
                interpreter = getattr(agent, "interpreter", None)
                _set_interpreter_default_profile(interpreter, runtime.cfg)
                session = _new_chat_session_state(runtime, identity)
                local_persist = _build_local_persist_fn(
                    state=state,
                    runtime=runtime,
                    agent=agent,
                    interpreter=interpreter,
                    session=session,
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
        logger.exception(
            "WebSocket execution startup failed: %s", _sanitize_for_log(exc)
        )
        if await _try_send_json(websocket, chat_startup_error_payload(exc)):
            await _close_websocket_safely(websocket, code=1011)


async def _run_execution_subscription_stream(
    *,
    websocket: WebSocket,
    state,
    identity,
    session_id: str,
) -> None:
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
            await websocket.receive()
    except WebSocketDisconnect:
        await emitter.disconnect(websocket)
    except Exception:
        logger.debug("execution_stream_receive_error", exc_info=True)
        await emitter.disconnect(websocket)


@router.websocket("/ws/execution")
async def execution_stream(
    websocket: WebSocket,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Canonical websocket endpoint for both execution streaming and subscriptions."""
    if await _reject_unsupported_identity_query_params(
        websocket,
        workspace_id=workspace_id,
        user_id=user_id,
    ):
        return

    state = get_server_state_from_websocket(websocket)
    identity = await _authenticate_websocket(websocket, state)
    if identity is None:
        return

    if not session_id:
        await _run_chat_execution_stream(
            websocket=websocket,
            state=state,
            identity=identity,
        )
        return

    await _run_execution_subscription_stream(
        websocket=websocket,
        state=state,
        identity=identity,
        session_id=session_id,
    )
