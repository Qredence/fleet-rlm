"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import asyncio
import logging
import time
import uuid
from typing import Any

import dspy
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from fleet_rlm import runners
from fleet_rlm.analytics.trace_context import (
    runtime_distinct_id_context,
    runtime_telemetry_enabled_context,
)
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db.models import (
    RunStatus,
)
from fleet_rlm.db.types import (
    RunCreateRequest,
)

from ..deps import server_state, session_key
from ..utils import parse_model_identity, resolve_sandbox_provider
from ..execution_events import (
    ExecutionStep,
    ExecutionStepBuilder,
    ExecutionSubscription,
)
from ..schemas import WSMessage


from .ws_helpers import (
    _sanitize_for_log,
    _sanitize_id,
    _authenticate_websocket,
    _get_execution_emitter,
    _error_envelope,
)
from .ws_session import _manifest_path, _volume_load_manifest, persist_session_state
from .ws_lifecycle import (
    ExecutionLifecycleManager,
    PersistenceRequiredError,
    _classify_stream_failure,
)
from .ws_commands import _handle_command


router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)

_REPL_HOOK_STEP_QUEUE_MAX = 128


def _should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def _enqueue_latest_nonblocking(queue: asyncio.Queue, item: object) -> bool:
    """Enqueue without blocking, dropping the oldest item when the queue is full."""
    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        pass

    try:
        _ = queue.get_nowait()
    except asyncio.QueueEmpty:
        return False

    try:
        queue.put_nowait(item)
        return True
    except asyncio.QueueFull:
        return False


@router.websocket("/ws/execution")
async def execution_stream(
    websocket: WebSocket,
    workspace_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
):
    """Dedicated execution stream for Artifact Canvas consumers."""
    identity = await _authenticate_websocket(websocket)
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
    emitter = _get_execution_emitter()
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
async def chat_streaming(websocket: WebSocket):
    """Streaming WebSocket endpoint with native DSPy async streaming."""
    identity = await _authenticate_websocket(websocket)
    if identity is None:
        return

    await websocket.accept()

    cfg = server_state.config
    _planner_lm = server_state.planner_lm
    _delegate_lm = server_state.delegate_lm
    repository = server_state.repository
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
            execution_emitter = _get_execution_emitter()
            ws_loop = asyncio.get_running_loop()
            last_loaded_docs_path: str | None = None

            async def local_persist(
                *, include_volume_save: bool = True, latest_user_message: str = ""
            ) -> None:
                await persist_session_state(
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
                    try:
                        msg = WSMessage(**raw_payload)
                    except ValidationError as exc:
                        raw_type = str(raw_payload.get("type", "")).strip()
                        if raw_type:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"Unknown message type: {raw_type}",
                                }
                            )
                            continue
                        await websocket.send_json(
                            {"type": "error", "message": f"Invalid payload: {exc}"}
                        )
                        continue

                    # Auth claims are canonical tenant/user authority for WS routes.
                    workspace_id = canonical_workspace_id
                    user_id = canonical_user_id
                    sess_id = msg.session_id or str(uuid.uuid4())
                    key = session_key(workspace_id, user_id, sess_id)
                    manifest_path = _manifest_path(workspace_id, user_id, sess_id)

                    # Switch/reload session identity if needed.
                    if active_key != key:
                        if session_record is not None:
                            await local_persist(include_volume_save=True)

                        cached = server_state.sessions.get(key)
                        if cached is None:
                            manifest = (
                                await _volume_load_manifest(agent, manifest_path)
                                if interpreter is not None
                                else {}
                            )
                            cached = {
                                "key": key,
                                "workspace_id": workspace_id,
                                "user_id": user_id,
                                "session_id": sess_id,
                                "manifest": manifest
                                if isinstance(manifest, dict)
                                else {},
                                "session": {"state": {}, "session_id": sess_id},
                            }
                        cached["session_id"] = sess_id
                        server_state.sessions[key] = cached
                        active_key = key
                        active_manifest_path = manifest_path
                        last_loaded_docs_path = None
                        session_record = cached
                        session_data = cached.get("session")
                        restored_state: Any = (
                            session_data.get("state", {})
                            if isinstance(session_data, dict)
                            else {}
                        )
                        manifest_data = cached.get("manifest")
                        if not restored_state and isinstance(manifest_data, dict):
                            restored_state = manifest_data.get("state", {})
                        if isinstance(restored_state, dict) and restored_state:
                            agent.import_session_state(restored_state)
                        else:
                            # No saved state — reset agent to prevent leaking
                            # prior session's history/docs across boundaries (#23).
                            agent.reset(clear_sandbox_buffers=True)

                    msg_type = msg.type

                    if msg_type == "cancel":
                        cancel_flag["cancelled"] = True
                        continue

                    if msg_type == "command":
                        await _handle_command(
                            websocket,
                            agent,
                            msg.model_dump(),
                            session_record,
                            repository=repository,
                            identity_rows=identity_rows,
                            persistence_required=persistence_required,
                        )
                        await local_persist(include_volume_save=True)
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
                    run_id = f"{workspace_id}:{user_id}:{sess_id}:{turn_index}"
                    step_builder = ExecutionStepBuilder(run_id=run_id)
                    active_run_db_id = None

                    if repository is None:
                        logger.warning(
                            "runtime_persistence_disabled_for_run",
                            extra={
                                "run_id": run_id,
                                "workspace_id": workspace_id,
                                "user_id": user_id,
                                "session_id": sess_id,
                                "code": "persistence_disabled",
                            },
                        )

                    if repository is not None and identity_rows is not None:
                        model_provider, model_name = parse_model_identity(
                            getattr(_planner_lm, "model", None)
                        )
                        try:
                            run_row = await repository.create_run(
                                RunCreateRequest(
                                    tenant_id=identity_rows.tenant_id,
                                    created_by_user_id=identity_rows.user_id,
                                    external_run_id=run_id,
                                    status=RunStatus.RUNNING,
                                    model_provider=model_provider,
                                    model_name=model_name,
                                    sandbox_provider=resolve_sandbox_provider(
                                        cfg.sandbox_provider
                                    ),
                                )
                            )
                            active_run_db_id = run_row.id
                            if session_record is not None:
                                session_record["last_run_db_id"] = str(run_row.id)
                        except Exception as exc:
                            if persistence_required:
                                raise PersistenceRequiredError(
                                    "run_start_persist_failed",
                                    f"Failed to persist run start: {exc}",
                                ) from exc
                            logger.warning(
                                "Failed to persist run start: %s",
                                _sanitize_for_log(exc),
                            )

                    lifecycle = ExecutionLifecycleManager(
                        run_id=run_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=sess_id,
                        execution_emitter=execution_emitter,
                        step_builder=step_builder,
                        repository=repository,
                        identity_rows=identity_rows,
                        active_run_db_id=active_run_db_id,
                        strict_persistence=persistence_required,
                        session_record=session_record,
                    )

                    def cancel_check() -> bool:
                        return cancel_flag["cancelled"]

                    await lifecycle.emit_started()

                    previous_execution_hook = None
                    repl_step_queue: asyncio.Queue[ExecutionStep | None] | None = None
                    repl_step_worker_task: asyncio.Task[None] | None = None

                    async def _emit_and_persist_repl_step(
                        step_data: ExecutionStep,
                    ) -> None:
                        if lifecycle.run_completed:
                            return
                        try:
                            await lifecycle.emit_step(step_data)
                            await lifecycle.persist_step(step_data)
                        except Exception as exc:
                            logger.warning(
                                "Failed to emit/persist REPL execution step: %s",
                                _sanitize_for_log(exc),
                            )
                            lifecycle._persistence_error = exc

                    async def _repl_step_worker() -> None:
                        assert repl_step_queue is not None
                        while True:
                            step_data = await repl_step_queue.get()
                            if step_data is None:
                                break
                            await _emit_and_persist_repl_step(step_data)

                    def _queue_repl_step(step_data: ExecutionStep) -> None:
                        if repl_step_queue is None or lifecycle.run_completed:
                            return
                        if not _enqueue_latest_nonblocking(repl_step_queue, step_data):
                            logger.debug(
                                "Dropped REPL execution step due to queue contention"
                            )

                    def _interpreter_hook(payload: dict[str, Any]) -> None:
                        if lifecycle.run_completed:
                            return
                        repl_step = step_builder.from_interpreter_hook(payload)
                        if repl_step is None:
                            return
                        ws_loop.call_soon_threadsafe(
                            lambda step_data=repl_step: _queue_repl_step(step_data)
                        )

                    repl_step_queue = asyncio.Queue(maxsize=_REPL_HOOK_STEP_QUEUE_MAX)
                    repl_step_worker_task = asyncio.create_task(_repl_step_worker())

                    if interpreter is not None:
                        previous_execution_hook = getattr(
                            interpreter, "execution_event_callback", None
                        )
                        interpreter.execution_event_callback = _interpreter_hook

                    if _should_reload_docs_path(last_loaded_docs_path, docs_path):
                        agent.load_document(str(docs_path))
                        last_loaded_docs_path = str(docs_path).strip()

                    runtime_analytics_enabled = getattr(msg, "analytics_enabled", None)
                    try:
                        with runtime_telemetry_enabled_context(
                            runtime_analytics_enabled
                        ):
                            async for event in agent.aiter_chat_turn_stream(
                                message=message, trace=trace, cancel_check=cancel_check
                            ):
                                lifecycle.raise_if_persistence_error()
                                event_dict = {
                                    "kind": event.kind,
                                    "text": event.text,
                                    "payload": event.payload,
                                    "timestamp": event.timestamp.isoformat(),
                                    "version": 2,
                                    "event_id": str(uuid.uuid4()),
                                }
                                is_terminal_event = event.kind in {
                                    "final",
                                    "cancelled",
                                    "error",
                                }
                                if not is_terminal_event:
                                    await websocket.send_json(
                                        {"type": "event", "data": event_dict}
                                    )

                                step = step_builder.from_stream_event(
                                    kind=event.kind,
                                    text=event.text,
                                    payload=event.payload,
                                    timestamp=event.timestamp.timestamp(),
                                )
                                if step is not None:
                                    await lifecycle.emit_step(step)
                                    await lifecycle.persist_step(step)
                                    lifecycle.raise_if_persistence_error()

                                if event.kind == "final":
                                    await local_persist(include_volume_save=True)
                                    await lifecycle.complete_run(
                                        RunStatus.COMPLETED, step=step
                                    )
                                    await websocket.send_json(
                                        {"type": "event", "data": event_dict}
                                    )
                                elif event.kind in {"cancelled", "error"}:
                                    status = (
                                        RunStatus.CANCELLED
                                        if event.kind == "cancelled"
                                        else RunStatus.FAILED
                                    )
                                    error_json = (
                                        {"error": event.text, "kind": event.kind}
                                        if event.kind == "error"
                                        else None
                                    )
                                    await lifecycle.complete_run(
                                        status, step=step, error_json=error_json
                                    )
                                    await websocket.send_json(
                                        {"type": "event", "data": event_dict}
                                    )

                        if not lifecycle.run_completed:
                            lifecycle.raise_if_persistence_error()
                            await lifecycle.complete_run(RunStatus.COMPLETED)

                    except Exception as exc:
                        error_code = _classify_stream_failure(exc)
                        logger.error(
                            "Streaming error: %s",
                            _sanitize_for_log(exc),
                            exc_info=True,
                            extra={
                                "error_type": type(exc).__name__,
                                "error_code": error_code,
                            },
                        )
                        await websocket.send_json(
                            _error_envelope(
                                code=error_code,
                                message=f"Streaming error: {exc}",
                                details={"error_type": type(exc).__name__},
                            )
                        )
                        if not lifecycle.run_completed:
                            error_step = step_builder.from_stream_event(
                                kind="error",
                                text=f"Streaming error: {exc}",
                                payload={
                                    "error_type": type(exc).__name__,
                                    "error_code": error_code,
                                },
                                timestamp=time.time(),
                            )
                            if error_step is not None:
                                await lifecycle.emit_step(error_step)
                            await lifecycle.complete_run(
                                RunStatus.FAILED,
                                step=error_step,
                                error_json={
                                    "error": str(exc),
                                    "error_type": type(exc).__name__,
                                    "code": error_code,
                                },
                            )
                    finally:
                        if interpreter is not None:
                            interpreter.execution_event_callback = (
                                previous_execution_hook
                            )
                        if repl_step_queue is not None:
                            await repl_step_queue.put(None)
                        if repl_step_worker_task is not None:
                            try:
                                await repl_step_worker_task
                            except asyncio.CancelledError:
                                pass

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
                except PersistenceRequiredError:
                    pass
                if lifecycle is not None:
                    await lifecycle.complete_run(
                        RunStatus.FAILED,
                        error_json={
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "code": error_code,
                        },
                    )
