"""WebSocket execution and subscription endpoints."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import asyncio
import logging
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fleet_rlm.integrations.observability.trace_context import (
    runtime_distinct_id_context,
)
from fleet_rlm.runtime.config import build_dspy_context
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

from ...auth import NormalizedIdentity
from ...dependencies import (
    AuthDeps,
    ConfigDeps,
    DiagnosticsDeps,
    LmDeps,
    PersistenceDeps,
    SessionCacheDeps,
    get_auth_deps_from_websocket,
    get_config_deps_from_websocket,
    get_diagnostics_deps_from_websocket,
    get_lm_deps_from_websocket,
    get_persistence_deps_from_websocket,
    get_session_cache_deps_from_websocket,
)
from ...events import ExecutionSubscription
from ...runtime_services.chat_persistence import (
    build_local_persist_fn as _build_local_persist_fn,
)
from ...runtime_services.chat_persistence import (
    cancel_startup_status_task as _cancel_startup_status_task,
)
from ...runtime_services.chat_persistence import (
    emit_delayed_startup_status as _emit_delayed_startup_status,
)
from ...runtime_services.chat_persistence import (
    get_execution_emitter,
)
from ...runtime_services.chat_runtime import (
    PreparedChatRuntime as _PreparedChatRuntime,
)
from ...runtime_services.chat_runtime import (
    build_chat_agent_context as _build_chat_agent_context,
)
from ...runtime_services.chat_runtime import (
    new_chat_session_state as _new_chat_session_state,
)
from ...runtime_services.chat_runtime import (
    prepare_chat_runtime as _prepare_chat_runtime_service,
)
from ...runtime_services.chat_runtime import (
    set_interpreter_default_profile as _set_interpreter_default_profile,
)
from .stream import WorkspaceEvent, _chat_message_loop, build_stream_event_dict
from .transport import (
    _authenticate_websocket,
    _close_websocket_safely,
    _error_envelope,
    _try_send_json,
    chat_startup_error_payload,
    parse_ws_message_or_send_error,
)

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


async def _prepare_chat_runtime(
    *,
    websocket: WebSocket,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    persistence_deps: PersistenceDeps,
    diagnostics_deps: DiagnosticsDeps,
    identity: NormalizedIdentity,
) -> _PreparedChatRuntime | None:
    async def _send_error(
        target: WebSocket,
        *,
        code: str,
        message: str,
    ) -> bool:
        return await _try_send_json(
            target,
            _error_envelope(code=code, message=message),
        )

    return await _prepare_chat_runtime_service(
        websocket=websocket,
        config_deps=config_deps,
        lm_deps=lm_deps,
        persistence_deps=persistence_deps,
        diagnostics_deps=diagnostics_deps,
        identity=identity,
        send_error=_send_error,
        close_websocket=_close_websocket_safely,
    )


_EXECUTION_STARTUP_STATUS_DELAY_SECONDS = 0.25


@dataclass(slots=True)
class _WebSocketCoreDeps:
    config: ConfigDeps
    auth: AuthDeps
    diagnostics: DiagnosticsDeps


@dataclass(slots=True)
class _ExecutionWebSocketDeps(_WebSocketCoreDeps):
    lm: LmDeps
    persistence: PersistenceDeps
    session_cache: SessionCacheDeps


def _resolve_websocket_core_deps(websocket: WebSocket) -> _WebSocketCoreDeps:
    """Resolve dependency slices shared by all websocket endpoints."""
    return _WebSocketCoreDeps(
        config=get_config_deps_from_websocket(websocket),
        auth=get_auth_deps_from_websocket(websocket),
        diagnostics=get_diagnostics_deps_from_websocket(websocket),
    )


def _resolve_execution_websocket_deps(websocket: WebSocket) -> _ExecutionWebSocketDeps:
    """Resolve the full dependency set needed by the conversational websocket."""
    core = _resolve_websocket_core_deps(websocket)
    return _ExecutionWebSocketDeps(
        config=core.config,
        auth=core.auth,
        diagnostics=core.diagnostics,
        lm=get_lm_deps_from_websocket(websocket),
        persistence=get_persistence_deps_from_websocket(websocket),
        session_cache=get_session_cache_deps_from_websocket(websocket),
    )


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


async def _reject_execution_query_session_id(
    websocket: WebSocket,
    *,
    session_id: str | None,
) -> bool:
    if not str(session_id or "").strip():
        return False

    await websocket.accept()
    if await _try_send_json(
        websocket,
        _error_envelope(
            code="execution_query_session_id_removed",
            message=(
                "Execution websocket no longer accepts query session_id. Send "
                "session_id in the message payload to resume chat sessions, or use "
                "/api/v1/ws/execution/events for passive subscriptions."
            ),
        ),
    ):
        await _close_websocket_safely(websocket, code=1008)
    return True


class _ExecutionWebSocketConnection:
    """Connection-scoped execution orchestration for one websocket client."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        config_deps: ConfigDeps,
        lm_deps: LmDeps,
        persistence_deps: PersistenceDeps,
        diagnostics_deps: DiagnosticsDeps,
        session_cache: SessionCacheDeps,
        identity: NormalizedIdentity,
    ) -> None:
        self.websocket = websocket
        self.config_deps = config_deps
        self.lm_deps = lm_deps
        self.persistence_deps = persistence_deps
        self.diagnostics_deps = diagnostics_deps
        self.session_cache = session_cache
        self.identity = identity

    async def _emit_delayed_startup_status(self) -> None:
        async def _emit_event(event: WorkspaceEvent) -> None:
            await _try_send_json(
                self.websocket,
                {
                    "type": "event",
                    "data": build_stream_event_dict(
                        event=event,
                        payload=event.payload,
                    ),
                },
            )

        await _emit_delayed_startup_status(
            delay_seconds=_EXECUTION_STARTUP_STATUS_DELAY_SECONDS,
            emit_event=_emit_event,
        )

    async def _cancel_startup_status_task(self, task: asyncio.Task[None] | None) -> None:
        await _cancel_startup_status_task(task)

    async def _receive_initial_message(self):
        initial_msg = None
        while initial_msg is None:
            raw_payload = await self.websocket.receive_json()
            initial_msg = await parse_ws_message_or_send_error(
                websocket=self.websocket,
                raw_payload=raw_payload,
            )
            if initial_msg is not None and initial_msg.type != "message":
                if await _try_send_json(
                    self.websocket,
                    _error_envelope(
                        code="initial_message_required",
                        message="Execution websocket must start with a canonical message frame.",
                    ),
                ):
                    await _close_websocket_safely(self.websocket, code=1008)
                return None
        return initial_msg

    async def run(self) -> None:
        await self.websocket.accept()
        runtime = await _prepare_chat_runtime(
            websocket=self.websocket,
            config_deps=self.config_deps,
            lm_deps=self.lm_deps,
            persistence_deps=self.persistence_deps,
            diagnostics_deps=self.diagnostics_deps,
            identity=self.identity,
        )
        if runtime is None:
            return

        analytics_distinct_id = (self.identity.user_claim or "").strip() or None
        startup_status_task: asyncio.Task[None] | None = None
        try:
            with (
                runtime_distinct_id_context(analytics_distinct_id),
                build_dspy_context(lm=runtime.planner_lm),
            ):
                initial_msg = await self._receive_initial_message()
                if initial_msg is None:
                    return
                if initial_msg.type == "message":
                    startup_status_task = asyncio.create_task(self._emit_delayed_startup_status())
                agent_context = await _build_chat_agent_context(runtime)
                async with agent_context as agent:
                    await self._cancel_startup_status_task(startup_status_task)
                    startup_status_task = None
                    interpreter = getattr(agent, "interpreter", None)
                    _set_interpreter_default_profile(interpreter, runtime.cfg)
                    session = _new_chat_session_state(runtime, self.identity)
                    local_persist = _build_local_persist_fn(
                        session_cache=self.session_cache,
                        runtime=runtime,
                        agent=agent,
                        interpreter=interpreter,
                        session=session,
                    )

                    # Connect to Event Bus for decoupled execution events
                    emitter = get_execution_emitter(self.diagnostics_deps)
                    subscription = ExecutionSubscription(
                        workspace_id=session.canonical_workspace_id,
                        user_id=session.canonical_user_id,
                        session_id="",
                    )
                    await emitter.connect(self.websocket, subscription, accept=False)

                    try:
                        await _chat_message_loop(
                            websocket=self.websocket,
                            session_cache=self.session_cache,
                            diagnostics_deps=self.diagnostics_deps,
                            runtime=runtime,
                            agent=agent,
                            interpreter=interpreter,
                            session=session,
                            local_persist=local_persist,
                            initial_message=initial_msg,
                        )
                    finally:
                        await emitter.disconnect(self.websocket)
        except (asyncio.CancelledError, WebSocketDisconnect):
            await self._cancel_startup_status_task(startup_status_task)
            return
        except Exception as exc:
            await self._cancel_startup_status_task(startup_status_task)
            logger.exception("WebSocket execution startup failed: %s", _sanitize_for_log(exc))
            if await _try_send_json(self.websocket, chat_startup_error_payload(exc)):
                await _close_websocket_safely(self.websocket, code=1011)


async def _run_execution_subscription_stream(
    *,
    websocket: WebSocket,
    diagnostics_deps: DiagnosticsDeps,
    identity: NormalizedIdentity,
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

    emitter = get_execution_emitter(diagnostics_deps)
    await emitter.connect(websocket, subscription)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") != "websocket.receive":
                continue
            if message.get("text") is None and message.get("bytes") is None:
                continue
            if await _try_send_json(
                websocket,
                _error_envelope(
                    code="passive_subscription_only",
                    message=(
                        "Passive execution event streams are subscription-only; "
                        "message, cancel, command, and start frames are rejected."
                    ),
                ),
            ):
                await _close_websocket_safely(websocket, code=1008)
            break
    except WebSocketDisconnect:
        await emitter.disconnect(websocket)
    except Exception:
        logger.debug("execution_stream_receive_error", exc_info=True)
        await emitter.disconnect(websocket)
    else:
        await emitter.disconnect(websocket)


@router.websocket("/ws/execution")
async def execution_stream(
    websocket: WebSocket,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Canonical websocket endpoint for execution streaming only."""
    if await _reject_unsupported_identity_query_params(
        websocket,
        workspace_id=workspace_id,
        user_id=user_id,
    ):
        return
    if await _reject_execution_query_session_id(websocket, session_id=session_id):
        return

    deps = _resolve_execution_websocket_deps(websocket)
    identity = await _authenticate_websocket(websocket, deps.config, deps.auth)
    if identity is None:
        return

    connection = _ExecutionWebSocketConnection(
        websocket=websocket,
        config_deps=deps.config,
        lm_deps=deps.lm,
        persistence_deps=deps.persistence,
        diagnostics_deps=deps.diagnostics,
        session_cache=deps.session_cache,
        identity=identity,
    )
    await connection.run()


@router.websocket("/ws/execution/events")
async def execution_events_stream(
    websocket: WebSocket,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Dedicated websocket endpoint for passive execution-event subscriptions."""
    if await _reject_unsupported_identity_query_params(
        websocket,
        workspace_id=workspace_id,
        user_id=user_id,
    ):
        return

    deps = _resolve_websocket_core_deps(websocket)
    identity = await _authenticate_websocket(websocket, deps.config, deps.auth)
    if identity is None:
        return

    await _run_execution_subscription_stream(
        websocket=websocket,
        diagnostics_deps=deps.diagnostics,
        identity=identity,
        session_id=str(session_id or "").strip(),
    )
