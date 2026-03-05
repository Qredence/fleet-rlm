"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import dspy
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from fleet_rlm import runners
from fleet_rlm.analytics.trace_context import (
    runtime_distinct_id_context,
)
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db.models import (
    RunStatus,
)

from ...deps import get_server_state_from_websocket
from ...execution import (
    ExecutionSubscription,
)


from .helpers import (
    _authenticate_websocket,
    _error_envelope,
    _get_execution_emitter,
    _sanitize_for_log,
    _sanitize_id,
)
from .lifecycle import (
    ExecutionLifecycleManager,
    PersistenceRequiredError,
    _classify_stream_failure,
)
from .message_loop import (
    parse_ws_message_or_send_error,
    resolve_session_identity,
    switch_session_if_needed,
)
from .session import persist_session_state
from .streaming import run_streaming_turn
from .turn import handle_command_with_persist, initialize_turn_lifecycle


router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _PreparedChatRuntime:
    cfg: Any
    planner_lm: Any
    delegate_lm: Any
    repository: Any
    persistence_required: bool
    identity_rows: Any


@dataclass(slots=True)
class _ChatSessionState:
    canonical_workspace_id: str
    canonical_user_id: str
    cancel_flag: dict[str, bool]
    active_key: str | None = None
    active_manifest_path: str | None = None
    session_record: dict[str, Any] | None = None
    active_run_db_id: uuid.UUID | None = None
    lifecycle: ExecutionLifecycleManager | None = None
    last_loaded_docs_path: str | None = None


def _set_interpreter_default_profile(interpreter: Any, cfg: Any) -> None:
    if interpreter is None:
        return
    try:
        interpreter.default_execution_profile = ExecutionProfile(
            cfg.ws_default_execution_profile
        )
    except ValueError:
        interpreter.default_execution_profile = ExecutionProfile.ROOT_INTERLOCUTOR


async def _prepare_chat_runtime(
    *,
    websocket: WebSocket,
    state: Any,
    identity: Any,
) -> _PreparedChatRuntime | None:
    cfg = state.config
    planner_lm = state.planner_lm
    delegate_lm = state.delegate_lm
    repository = state.repository
    persistence_required = cfg.database_required
    identity_rows = None

    if repository is not None:
        identity_rows = await repository.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )
    elif persistence_required:
        await websocket.send_json(
            _error_envelope(
                code="durable_state_unavailable",
                message="Database repository is required but unavailable",
            )
        )
        await websocket.close(code=1011)
        return None

    if planner_lm is None:
        await websocket.send_json(
            _error_envelope(
                code="planner_missing",
                message=(
                    "Planner LM not configured. "
                    "Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars."
                ),
            )
        )
        await websocket.close()
        return None

    return _PreparedChatRuntime(
        cfg=cfg,
        planner_lm=planner_lm,
        delegate_lm=delegate_lm,
        repository=repository,
        persistence_required=persistence_required,
        identity_rows=identity_rows,
    )


def _build_chat_agent_context(runtime: _PreparedChatRuntime) -> Any:
    return runners.build_react_chat_agent(
        react_max_iters=runtime.cfg.react_max_iters,
        deep_react_max_iters=runtime.cfg.deep_react_max_iters,
        enable_adaptive_iters=runtime.cfg.enable_adaptive_iters,
        rlm_max_iterations=runtime.cfg.rlm_max_iterations,
        rlm_max_llm_calls=runtime.cfg.rlm_max_llm_calls,
        max_depth=runtime.cfg.rlm_max_depth,
        timeout=runtime.cfg.timeout,
        secret_name=runtime.cfg.secret_name,
        volume_name=runtime.cfg.volume_name,
        interpreter_async_execute=runtime.cfg.interpreter_async_execute,
        guardrail_mode=runtime.cfg.agent_guardrail_mode,
        max_output_chars=runtime.cfg.agent_max_output_chars,
        min_substantive_chars=runtime.cfg.agent_min_substantive_chars,
        planner_lm=runtime.planner_lm,
        delegate_lm=runtime.delegate_lm,
        delegate_max_calls_per_turn=runtime.cfg.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=runtime.cfg.delegate_result_truncation_chars,
    )


def _new_chat_session_state(
    runtime: _PreparedChatRuntime, identity: Any
) -> _ChatSessionState:
    return _ChatSessionState(
        canonical_workspace_id=_sanitize_id(
            identity.tenant_claim, runtime.cfg.ws_default_workspace_id
        ),
        canonical_user_id=_sanitize_id(
            identity.user_claim, runtime.cfg.ws_default_user_id
        ),
        cancel_flag={"cancelled": False},
    )


def _chat_startup_error_payload(exc: Exception) -> dict[str, Any]:
    """Build a stable websocket error envelope for startup failures."""
    error_code = _classify_stream_failure(exc)
    lowered = str(exc).lower()

    if "token id is malformed" in lowered and "modal" in lowered:
        message = (
            "Modal authentication failed: Token ID is malformed. "
            "Update MODAL_TOKEN_ID / MODAL_TOKEN_SECRET or run `uv run modal token set`, "
            "then restart the server."
        )
    else:
        message = f"Server error: {str(exc)}"

    return _error_envelope(
        code=error_code,
        message=message,
        details={"error_type": type(exc).__name__},
    )


async def _process_chat_message(
    *,
    websocket: WebSocket,
    msg: Any,
    agent: Any,
    interpreter: Any,
    session: _ChatSessionState,
    local_persist: Any,
    runtime: _PreparedChatRuntime,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    execution_emitter: Any,
) -> str | None:
    """Process one ``message`` payload and return the loaded docs path."""
    message = str(msg.content or "").strip()
    if not message:
        await websocket.send_json(
            {"type": "error", "message": "Message content cannot be empty"}
        )
        return session.last_loaded_docs_path

    await local_persist(include_volume_save=True, latest_user_message=message)
    session.cancel_flag["cancelled"] = False
    turn_index = agent.history_turns() + 1
    (
        session.lifecycle,
        step_builder,
        _run_id,
        session.active_run_db_id,
    ) = await initialize_turn_lifecycle(
        planner_lm=runtime.planner_lm,
        cfg=runtime.cfg,
        repository=runtime.repository,
        identity_rows=runtime.identity_rows,
        persistence_required=runtime.persistence_required,
        execution_emitter=execution_emitter,
        workspace_id=workspace_id,
        user_id=user_id,
        sess_id=sess_id,
        turn_index=turn_index,
        session_record=session.session_record,
    )

    def cancel_check() -> bool:
        return session.cancel_flag["cancelled"]

    return await run_streaming_turn(
        websocket=websocket,
        agent=agent,
        message=message,
        docs_path=msg.docs_path,
        trace=bool(msg.trace),
        cancel_check=cancel_check,
        lifecycle=session.lifecycle,
        step_builder=step_builder,
        interpreter=interpreter,
        last_loaded_docs_path=session.last_loaded_docs_path,
        analytics_enabled=getattr(msg, "analytics_enabled", None),
        persist_session_state=local_persist,
    )


async def _chat_message_loop(
    *,
    websocket: WebSocket,
    state: Any,
    runtime: _PreparedChatRuntime,
    agent: Any,
    interpreter: Any,
    session: _ChatSessionState,
    local_persist: Any,
) -> None:
    execution_emitter = _get_execution_emitter(state)

    try:
        while True:
            raw_payload = await websocket.receive_json()
            msg = await parse_ws_message_or_send_error(
                websocket=websocket,
                raw_payload=raw_payload,
            )
            if msg is None:
                continue

            workspace_id, user_id, sess_id = resolve_session_identity(
                msg=msg,
                workspace_id=session.canonical_workspace_id,
                user_id=session.canonical_user_id,
            )
            (
                session.active_key,
                session.active_manifest_path,
                session.session_record,
                session.last_loaded_docs_path,
            ) = await switch_session_if_needed(
                state=state,
                agent=agent,
                interpreter=interpreter,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
                active_key=session.active_key,
                session_record=session.session_record,
                last_loaded_docs_path=session.last_loaded_docs_path,
                local_persist=local_persist,
            )

            if msg.type == "cancel":
                session.cancel_flag["cancelled"] = True
                continue

            if msg.type == "command":
                await handle_command_with_persist(
                    websocket=websocket,
                    agent=agent,
                    payload=msg.model_dump(),
                    session_record=session.session_record,
                    repository=runtime.repository,
                    identity_rows=runtime.identity_rows,
                    persistence_required=runtime.persistence_required,
                    local_persist=local_persist,
                )
                continue

            if msg.type != "message":
                await websocket.send_json(
                    {"type": "error", "message": f"Unknown message type: {msg.type}"}
                )
                continue

            session.last_loaded_docs_path = await _process_chat_message(
                websocket=websocket,
                msg=msg,
                agent=agent,
                interpreter=interpreter,
                session=session,
                local_persist=local_persist,
                runtime=runtime,
                workspace_id=workspace_id,
                user_id=user_id,
                sess_id=sess_id,
                execution_emitter=execution_emitter,
            )
    except WebSocketDisconnect:
        session.cancel_flag["cancelled"] = True
        try:
            await local_persist(include_volume_save=True)
        except PersistenceRequiredError as exc:
            logger.warning(
                "Session persistence failed during disconnect: %s",
                _sanitize_for_log(exc),
            )
            if session.lifecycle is not None and not session.lifecycle.run_completed:
                await session.lifecycle.complete_run(
                    RunStatus.FAILED,
                    error_json={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "code": exc.code,
                    },
                )
            return
        if session.lifecycle is not None:
            await session.lifecycle.complete_run(RunStatus.CANCELLED)
    except Exception as exc:
        error_code = _classify_stream_failure(exc)
        await websocket.send_json(
            _error_envelope(
                code=error_code,
                message=f"Server error: {str(exc)}",
                details={"error_type": type(exc).__name__},
            )
        )
        try:
            await local_persist(include_volume_save=True)
        except PersistenceRequiredError as persist_exc:
            logger.warning(
                "Session persistence failed after stream error: %s",
                _sanitize_for_log(persist_exc),
            )
        if session.lifecycle is not None:
            await session.lifecycle.complete_run(
                RunStatus.FAILED,
                error_json={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "code": error_code,
                },
            )


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
        await websocket.send_json(
            _error_envelope(
                code="missing_session_id",
                message="Missing required query param: session_id",
            )
        )
        await websocket.close(code=1008)
        return
    emitter = _get_execution_emitter(state)
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

    agent_context = _build_chat_agent_context(runtime)

    analytics_distinct_id = (identity.user_claim or "").strip() or None
    try:
        with (
            runtime_distinct_id_context(analytics_distinct_id),
            dspy.context(lm=runtime.planner_lm),
        ):
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
                )
    except Exception as exc:
        logger.exception("WebSocket chat startup failed: %s", _sanitize_for_log(exc))
        await websocket.send_json(_chat_startup_error_payload(exc))
        await websocket.close(code=1011)
