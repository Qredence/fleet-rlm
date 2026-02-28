"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import logging
import uuid
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

    cfg = state.config
    _planner_lm = state.planner_lm
    _delegate_lm = state.delegate_lm
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
        return

    if _planner_lm is None:
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
        return

    agent_context = runners.build_react_chat_agent(
        react_max_iters=cfg.react_max_iters,
        deep_react_max_iters=cfg.deep_react_max_iters,
        enable_adaptive_iters=cfg.enable_adaptive_iters,
        rlm_max_iterations=cfg.rlm_max_iterations,
        rlm_max_llm_calls=cfg.rlm_max_llm_calls,
        max_depth=cfg.rlm_max_depth,
        timeout=cfg.timeout,
        secret_name=cfg.secret_name,
        volume_name=cfg.volume_name,
        interpreter_async_execute=cfg.interpreter_async_execute,
        guardrail_mode=cfg.agent_guardrail_mode,
        max_output_chars=cfg.agent_max_output_chars,
        min_substantive_chars=cfg.agent_min_substantive_chars,
        planner_lm=_planner_lm,
        delegate_lm=_delegate_lm,
        delegate_max_calls_per_turn=cfg.delegate_max_calls_per_turn,
        delegate_result_truncation_chars=cfg.delegate_result_truncation_chars,
    )

    analytics_distinct_id = (identity.user_claim or "").strip() or None
    with (
        runtime_distinct_id_context(analytics_distinct_id),
        dspy.context(lm=_planner_lm),
    ):
        async with agent_context as agent:
            interpreter = getattr(agent, "interpreter", None)
            # Interlocutor path defaults to strict root profile.
            if interpreter is not None:
                try:
                    interpreter.default_execution_profile = ExecutionProfile(
                        cfg.ws_default_execution_profile
                    )
                except ValueError:
                    interpreter.default_execution_profile = (
                        ExecutionProfile.ROOT_INTERLOCUTOR
                    )

            cancel_flag = {"cancelled": False}
            active_key: str | None = None
            active_manifest_path: str | None = None
            canonical_workspace_id = _sanitize_id(
                identity.tenant_claim, cfg.ws_default_workspace_id
            )
            canonical_user_id = _sanitize_id(
                identity.user_claim, cfg.ws_default_user_id
            )
            session_record: dict[str, Any] | None = None
            active_run_db_id: uuid.UUID | None = None
            lifecycle: ExecutionLifecycleManager | None = None
            execution_emitter = _get_execution_emitter(state)
            last_loaded_docs_path: str | None = None

            async def local_persist(
                *, include_volume_save: bool = True, latest_user_message: str = ""
            ) -> None:
                await persist_session_state(
                    state=state,
                    agent=agent,
                    session_record=session_record,
                    active_manifest_path=active_manifest_path,
                    active_run_db_id=active_run_db_id,
                    interpreter=interpreter,
                    repository=repository,
                    identity_rows=identity_rows,
                    persistence_required=persistence_required,
                    include_volume_save=include_volume_save,
                    latest_user_message=latest_user_message,
                )

            try:
                while True:
                    raw_payload = await websocket.receive_json()
                    msg = await parse_ws_message_or_send_error(
                        websocket=websocket,
                        raw_payload=raw_payload,
                    )
                    if msg is None:
                        continue

                    # Auth claims are canonical tenant/user authority for WS routes.
                    workspace_id, user_id, sess_id = resolve_session_identity(
                        msg=msg,
                        workspace_id=canonical_workspace_id,
                        user_id=canonical_user_id,
                    )
                    (
                        key,
                        manifest_path,
                        cached_session,
                        last_loaded_docs_path,
                    ) = await switch_session_if_needed(
                        state=state,
                        agent=agent,
                        interpreter=interpreter,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sess_id=sess_id,
                        active_key=active_key,
                        session_record=session_record,
                        last_loaded_docs_path=last_loaded_docs_path,
                        local_persist=local_persist,
                    )
                    active_key = key
                    active_manifest_path = manifest_path
                    session_record = cached_session

                    msg_type = msg.type

                    if msg_type == "cancel":
                        cancel_flag["cancelled"] = True
                        continue

                    if msg_type == "command":
                        await handle_command_with_persist(
                            websocket=websocket,
                            agent=agent,
                            payload=msg.model_dump(),
                            session_record=session_record,
                            repository=repository,
                            identity_rows=identity_rows,
                            persistence_required=persistence_required,
                            local_persist=local_persist,
                        )
                        continue

                    if msg_type != "message":
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": f"Unknown message type: {msg_type}",
                            }
                        )
                        continue

                    message = str(msg.content or "").strip()
                    docs_path = msg.docs_path
                    trace = bool(msg.trace)

                    if not message:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "Message content cannot be empty",
                            }
                        )
                        continue

                    await local_persist(
                        include_volume_save=True, latest_user_message=message
                    )
                    cancel_flag["cancelled"] = False
                    turn_index = agent.history_turns() + 1
                    (
                        lifecycle,
                        step_builder,
                        _run_id,
                        active_run_db_id,
                    ) = await initialize_turn_lifecycle(
                        planner_lm=_planner_lm,
                        cfg=cfg,
                        repository=repository,
                        identity_rows=identity_rows,
                        persistence_required=persistence_required,
                        execution_emitter=execution_emitter,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        sess_id=sess_id,
                        turn_index=turn_index,
                        session_record=session_record,
                    )

                    def cancel_check() -> bool:
                        return cancel_flag["cancelled"]

                    runtime_analytics_enabled = getattr(msg, "analytics_enabled", None)
                    last_loaded_docs_path = await run_streaming_turn(
                        websocket=websocket,
                        agent=agent,
                        message=message,
                        docs_path=docs_path,
                        trace=trace,
                        cancel_check=cancel_check,
                        lifecycle=lifecycle,
                        step_builder=step_builder,
                        interpreter=interpreter,
                        last_loaded_docs_path=last_loaded_docs_path,
                        analytics_enabled=runtime_analytics_enabled,
                        persist_session_state=local_persist,
                    )

            except WebSocketDisconnect:
                cancel_flag["cancelled"] = True
                try:
                    await local_persist(include_volume_save=True)
                except PersistenceRequiredError as exc:
                    logger.warning(
                        "Session persistence failed during disconnect: %s",
                        _sanitize_for_log(exc),
                    )
                    if lifecycle is not None and not lifecycle.run_completed:
                        await lifecycle.complete_run(
                            RunStatus.FAILED,
                            error_json={
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "code": exc.code,
                            },
                        )
                    return
                if lifecycle is not None:
                    await lifecycle.complete_run(RunStatus.CANCELLED)
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
                if lifecycle is not None:
                    await lifecycle.complete_run(
                        RunStatus.FAILED,
                        error_json={
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "code": error_code,
                        },
                    )
