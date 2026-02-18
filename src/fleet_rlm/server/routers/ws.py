"""WebSocket streaming chat endpoint."""

# NOTE: Do NOT add ``from __future__ import annotations`` here.
# FastAPI inspects handler parameter *types* at runtime to detect
# ``WebSocket`` vs query params.  PEP 604 stringified annotations break
# that introspection, causing WebSocket endpoints to reject connections
# with HTTP 403 ("Field required" for a query param named ``websocket``).

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import dspy
from dspy.primitives.code_interpreter import FinalOutput
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from fleet_rlm import runners
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
    SandboxProvider,
)
from fleet_rlm.db.types import (
    ArtifactCreateRequest,
    IdentityUpsertResult,
    MemoryItemCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)

from ..auth import AuthError
from ..deps import server_state, session_key
from ..execution_events import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionStep,
    ExecutionStepBuilder,
    ExecutionSubscription,
)
from ..schemas import WSMessage

router = APIRouter(tags=["websocket"])

logger = logging.getLogger(__name__)

EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


def _sanitize_for_log(value: object) -> str:
    """Normalize untrusted values to a single log line."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _sanitize_id(value: str, default_value: str) -> str:
    """Restrict workspace/user IDs to a safe path/key subset."""
    candidate = (value or "").strip()
    if not candidate:
        return default_value
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)
    return cleaned[:128] or default_value


def _manifest_path(workspace_id: str, user_id: str) -> str:
    return f"workspaces/{workspace_id}/users/{user_id}/memory/react-session.json"


def _volume_load_manifest(agent: "runners.RLMReActChatAgent", path: str) -> dict:
    """Best-effort manifest load from Modal volume; returns empty dict if absent."""
    result = agent.interpreter.execute(
        "text = load_from_volume(path)\nSUBMIT(text=text)",
        variables={"path": path},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return {}
    output = result.output if isinstance(result.output, dict) else {}
    text = str(output.get("text", ""))
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _volume_save_manifest(
    agent: "runners.RLMReActChatAgent", path: str, manifest: dict
) -> str | None:
    """Best-effort manifest save to Modal volume."""
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    result = agent.interpreter.execute(
        "saved_path = save_to_volume(path, payload)\nSUBMIT(saved_path=saved_path)",
        variables={"path": path, "payload": payload},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not isinstance(result, FinalOutput):
        return None
    output = result.output if isinstance(result.output, dict) else {}
    saved_path = str(output.get("saved_path", ""))
    if saved_path.startswith("["):
        return None
    return saved_path or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _authenticate_websocket(
    websocket: WebSocket,
):
    provider = server_state.auth_provider
    if provider is None:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "Auth provider missing"})
        await websocket.close(code=1011)
        return None

    try:
        return await provider.authenticate_websocket(websocket)
    except AuthError as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": exc.message})
        await websocket.close(code=1008)
        return None


def _parse_model_identity(raw_model: object) -> tuple[str | None, str | None]:
    if not isinstance(raw_model, str):
        return None, None
    if "/" in raw_model:
        provider, name = raw_model.split("/", 1)
        return provider, name
    return None, raw_model


def _map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


def _get_execution_emitter():
    emitter = server_state.execution_event_emitter
    if emitter is not None:
        return emitter

    from ..execution_events import ExecutionEventEmitter

    emitter = ExecutionEventEmitter()
    server_state.execution_event_emitter = emitter
    return emitter


def _build_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    step: ExecutionStep | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        step=step,
    )


async def _emit_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    step: ExecutionStep | None = None,
) -> None:
    event = _build_execution_event(
        event_type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        step=step,
    )
    await _get_execution_emitter().emit(event)


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
            {
                "type": "error",
                "message": "Missing required query param: session_id",
            }
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
    repository = server_state.repository
    identity_rows = None
    if repository is not None:
        identity_rows = await repository.upsert_identity(
            entra_tenant_id=identity.tenant_claim,
            entra_user_id=identity.user_claim,
            email=identity.email,
            full_name=identity.name,
        )

    if _planner_lm is None:
        await websocket.send_json(
            {
                "type": "error",
                "message": "Planner LM not configured. Check DSPY_LM_MODEL and DSPY_LLM_API_KEY env vars.",
            }
        )
        await websocket.close()
        return

    agent_context = runners.build_react_chat_agent(
        react_max_iters=cfg.react_max_iters,
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
    )

    with dspy.context(lm=_planner_lm), agent_context as agent:
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
        canonical_user_id = _sanitize_id(identity.user_claim, cfg.ws_default_user_id)
        session_record: dict[str, Any] | None = None
        active_run_db_id: uuid.UUID | None = None
        active_step_index = 0
        active_step_lock = asyncio.Lock()
        active_last_step_db_id: uuid.UUID | None = None
        execution_emitter = _get_execution_emitter()
        ws_loop = asyncio.get_running_loop()

        async def persist_session_state(
            *, include_volume_save: bool = True, latest_user_message: str = ""
        ) -> None:
            nonlocal session_record, active_manifest_path, active_run_db_id
            if session_record is None:
                return
            exported_state = agent.export_session_state()
            manifest = session_record.get("manifest")
            if not isinstance(manifest, dict):
                manifest = {}
                session_record["manifest"] = manifest

            logs = manifest.get("logs")
            if not isinstance(logs, list):
                logs = []
                manifest["logs"] = logs

            memory = manifest.get("memory")
            if not isinstance(memory, list):
                memory = []
                manifest["memory"] = memory

            generated_docs = manifest.get("generated_docs")
            if not isinstance(generated_docs, list):
                generated_docs = []
                manifest["generated_docs"] = generated_docs

            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, list):
                artifacts = []
                manifest["artifacts"] = artifacts

            metadata = manifest.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                manifest["metadata"] = metadata

            if latest_user_message:
                logs.append(
                    {
                        "timestamp": _now_iso(),
                        "user_message": latest_user_message,
                        "history_turns": len(exported_state.get("history", [])),
                    }
                )
                # Lightweight conversational memory snapshot.
                memory.append(
                    {
                        "timestamp": _now_iso(),
                        "content": latest_user_message[:400],
                    }
                )

            generated_docs[:] = sorted(list(exported_state.get("documents", {}).keys()))
            metadata["updated_at"] = _now_iso()
            metadata["history_turns"] = len(exported_state.get("history", []))
            metadata["document_count"] = len(exported_state.get("documents", {}))
            metadata["artifact_count"] = len(artifacts)
            manifest["state"] = (
                exported_state  # Persist full state for volume restore (#24)
            )
            session_data = session_record.get("session")
            if not isinstance(session_data, dict):
                session_data = {}
                session_record["session"] = session_data
            session_data["state"] = exported_state
            session_data["session_id"] = session_record.get("session_id")

            record_key = session_record.get("key")
            if isinstance(record_key, str):
                server_state.sessions[record_key] = session_record

            if include_volume_save and active_manifest_path and interpreter is not None:
                _volume_save_manifest(agent, active_manifest_path, manifest)

            if (
                latest_user_message
                and repository is not None
                and identity_rows is not None
            ):
                try:
                    await repository.store_memory_item(
                        MemoryItemCreateRequest(
                            tenant_id=identity_rows.tenant_id,
                            scope=MemoryScope.RUN
                            if active_run_db_id is not None
                            else MemoryScope.USER,
                            scope_id=str(active_run_db_id or identity_rows.user_id),
                            kind=MemoryKind.NOTE,
                            source=MemorySource.USER_INPUT,
                            content_text=latest_user_message[:1000],
                            tags=["ws", "chat"],
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to persist memory item: %s",
                        _sanitize_for_log(exc),
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
                key = session_key(workspace_id, user_id)
                manifest_path = _manifest_path(workspace_id, user_id)

                # Switch/reload session identity if needed.
                if active_key != key:
                    if session_record is not None:
                        await persist_session_state(include_volume_save=True)

                    cached = server_state.sessions.get(key)
                    if cached is None:
                        manifest = (
                            _volume_load_manifest(agent, manifest_path)
                            if interpreter is not None
                            else {}
                        )
                        cached = {
                            "key": key,
                            "workspace_id": workspace_id,
                            "user_id": user_id,
                            "session_id": sess_id,
                            "manifest": manifest if isinstance(manifest, dict) else {},
                            "session": {"state": {}, "session_id": sess_id},
                        }
                    cached["session_id"] = sess_id
                    server_state.sessions[key] = cached
                    active_key = key
                    active_manifest_path = manifest_path
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
                    )
                    await persist_session_state(include_volume_save=True)
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

                cancel_flag["cancelled"] = False
                turn_index = agent.history_turns() + 1
                run_id = f"{workspace_id}:{user_id}:{sess_id}:{turn_index}"
                step_builder = ExecutionStepBuilder(run_id=run_id)
                run_completed = False
                active_run_db_id = None
                active_step_index = 0
                active_last_step_db_id = None

                if repository is not None and identity_rows is not None:
                    model_provider, model_name = _parse_model_identity(
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
                                sandbox_provider=SandboxProvider.MODAL,
                            )
                        )
                        active_run_db_id = run_row.id
                        if session_record is not None:
                            session_record["last_run_db_id"] = str(run_row.id)
                    except Exception as exc:
                        logger.warning(
                            "Failed to persist run start: %s",
                            _sanitize_for_log(exc),
                        )

                def cancel_check() -> bool:
                    return cancel_flag["cancelled"]

                async def persist_execution_step(step: ExecutionStep | None) -> None:
                    nonlocal active_step_index, active_last_step_db_id
                    if (
                        step is None
                        or repository is None
                        or identity_rows is None
                        or active_run_db_id is None
                    ):
                        return
                    async with active_step_lock:
                        active_step_index += 1
                        try:
                            persisted = await repository.append_step(
                                RunStepCreateRequest(
                                    tenant_id=identity_rows.tenant_id,
                                    run_id=active_run_db_id,
                                    step_index=active_step_index,
                                    step_type=_map_execution_step_type(step.type),
                                    input_json=step.input
                                    if isinstance(step.input, dict)
                                    else {"value": step.input}
                                    if step.input is not None
                                    else None,
                                    output_json=step.output
                                    if isinstance(step.output, dict)
                                    else {"value": step.output}
                                    if step.output is not None
                                    else None,
                                )
                            )
                            active_last_step_db_id = persisted.id
                            if session_record is not None:
                                session_record["last_step_db_id"] = str(persisted.id)
                        except Exception as exc:
                            logger.warning(
                                "Failed to persist run step: %s",
                                _sanitize_for_log(exc),
                            )

                await execution_emitter.emit(
                    _build_execution_event(
                        event_type="execution_started",
                        run_id=run_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=sess_id,
                        step=None,
                    )
                )

                previous_execution_hook = None

                def _interpreter_hook(payload: dict[str, Any]) -> None:
                    if run_completed:
                        return
                    repl_step = step_builder.from_interpreter_hook(payload)
                    if repl_step is None:
                        return
                    event = _build_execution_event(
                        event_type="execution_step",
                        run_id=run_id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        session_id=sess_id,
                        step=repl_step,
                    )
                    ws_loop.call_soon_threadsafe(
                        lambda evt=event, step_data=repl_step: (
                            ws_loop.create_task(execution_emitter.emit(evt)),
                            ws_loop.create_task(persist_execution_step(step_data)),
                        )
                    )

                if interpreter is not None:
                    previous_execution_hook = getattr(
                        interpreter, "execution_event_callback", None
                    )
                    interpreter.execution_event_callback = _interpreter_hook

                if docs_path:
                    agent.load_document(docs_path)

                try:
                    async for event in agent.aiter_chat_turn_stream(
                        message=message, trace=trace, cancel_check=cancel_check
                    ):
                        event_dict = {
                            "kind": event.kind,
                            "text": event.text,
                            "payload": event.payload,
                            "timestamp": event.timestamp.isoformat(),
                        }
                        await websocket.send_json({"type": "event", "data": event_dict})

                        step = step_builder.from_stream_event(
                            kind=event.kind,
                            text=event.text,
                            payload=event.payload,
                            timestamp=event.timestamp.timestamp(),
                        )
                        if step is not None:
                            await execution_emitter.emit(
                                _build_execution_event(
                                    event_type="execution_step",
                                    run_id=run_id,
                                    workspace_id=workspace_id,
                                    user_id=user_id,
                                    session_id=sess_id,
                                    step=step,
                                )
                            )
                            await persist_execution_step(step)

                        if event.kind == "final":
                            await persist_session_state(
                                include_volume_save=True, latest_user_message=message
                            )
                            if (
                                repository is not None
                                and identity_rows is not None
                                and active_run_db_id is not None
                            ):
                                await repository.update_run_status(
                                    tenant_id=identity_rows.tenant_id,
                                    run_id=active_run_db_id,
                                    status=RunStatus.COMPLETED,
                                )
                            await execution_emitter.emit(
                                _build_execution_event(
                                    event_type="execution_completed",
                                    run_id=run_id,
                                    workspace_id=workspace_id,
                                    user_id=user_id,
                                    session_id=sess_id,
                                    step=step,
                                )
                            )
                            run_completed = True
                        elif event.kind in {"cancelled", "error"}:
                            if (
                                repository is not None
                                and identity_rows is not None
                                and active_run_db_id is not None
                            ):
                                await repository.update_run_status(
                                    tenant_id=identity_rows.tenant_id,
                                    run_id=active_run_db_id,
                                    status=RunStatus.CANCELLED
                                    if event.kind == "cancelled"
                                    else RunStatus.FAILED,
                                    error_json=(
                                        {"error": event.text, "kind": event.kind}
                                        if event.kind == "error"
                                        else None
                                    ),
                                )
                            await execution_emitter.emit(
                                _build_execution_event(
                                    event_type="execution_completed",
                                    run_id=run_id,
                                    workspace_id=workspace_id,
                                    user_id=user_id,
                                    session_id=sess_id,
                                    step=step,
                                )
                            )
                            run_completed = True
                    if not run_completed:
                        if (
                            repository is not None
                            and identity_rows is not None
                            and active_run_db_id is not None
                        ):
                            await repository.update_run_status(
                                tenant_id=identity_rows.tenant_id,
                                run_id=active_run_db_id,
                                status=RunStatus.COMPLETED,
                            )
                        await execution_emitter.emit(
                            _build_execution_event(
                                event_type="execution_completed",
                                run_id=run_id,
                                workspace_id=workspace_id,
                                user_id=user_id,
                                session_id=sess_id,
                                step=None,
                            )
                        )
                        run_completed = True
                except Exception as exc:
                    logger.error(
                        "Streaming error: %s",
                        _sanitize_for_log(exc),
                        exc_info=True,
                        extra={"error_type": type(exc).__name__},
                    )
                    await websocket.send_json(
                        {"type": "error", "message": f"Streaming error: {exc}"}
                    )
                    if not run_completed:
                        error_step = step_builder.from_stream_event(
                            kind="error",
                            text=f"Streaming error: {exc}",
                            payload={"error_type": type(exc).__name__},
                            timestamp=time.time(),
                        )
                        if error_step is not None:
                            await execution_emitter.emit(
                                _build_execution_event(
                                    event_type="execution_step",
                                    run_id=run_id,
                                    workspace_id=workspace_id,
                                    user_id=user_id,
                                    session_id=sess_id,
                                    step=error_step,
                                )
                            )
                        await execution_emitter.emit(
                            _build_execution_event(
                                event_type="execution_completed",
                                run_id=run_id,
                                workspace_id=workspace_id,
                                user_id=user_id,
                                session_id=sess_id,
                                step=error_step,
                            )
                        )
                        if (
                            repository is not None
                            and identity_rows is not None
                            and active_run_db_id is not None
                        ):
                            await repository.update_run_status(
                                tenant_id=identity_rows.tenant_id,
                                run_id=active_run_db_id,
                                status=RunStatus.FAILED,
                                error_json={
                                    "error": str(exc),
                                    "error_type": type(exc).__name__,
                                },
                            )
                        run_completed = True
                finally:
                    if interpreter is not None:
                        interpreter.execution_event_callback = previous_execution_hook

        except WebSocketDisconnect:
            cancel_flag["cancelled"] = True
            await persist_session_state(include_volume_save=True)
            if (
                repository is not None
                and identity_rows is not None
                and active_run_db_id is not None
            ):
                await repository.update_run_status(
                    tenant_id=identity_rows.tenant_id,
                    run_id=active_run_db_id,
                    status=RunStatus.CANCELLED,
                )
        except Exception as exc:
            await websocket.send_json(
                {"type": "error", "message": f"Server error: {str(exc)}"}
            )
            await persist_session_state(include_volume_save=True)
            if (
                repository is not None
                and identity_rows is not None
                and active_run_db_id is not None
            ):
                await repository.update_run_status(
                    tenant_id=identity_rows.tenant_id,
                    run_id=active_run_db_id,
                    status=RunStatus.FAILED,
                    error_json={
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )


async def _handle_command(
    websocket: WebSocket,
    agent: "runners.RLMReActChatAgent",
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    *,
    repository: FleetRepository | None = None,
    identity_rows: IdentityUpsertResult | None = None,
) -> None:
    """Dispatch a command message to the agent and return the result."""
    command = str(payload.get("command", "")).strip()
    args = payload.get("args", {})

    if not command:
        await websocket.send_json(
            {"type": "error", "message": "Command name cannot be empty"}
        )
        return

    try:
        with agent.interpreter.execution_profile(ExecutionProfile.RLM_DELEGATE):
            result = await agent.execute_command(command, args)

        # Track likely artifact writes as session metadata.
        if session_record is not None and command in {
            "save_buffer",
            "load_volume",
            "write_to_file",
        }:
            manifest = session_record.get("manifest")
            if not isinstance(manifest, dict):
                manifest = {}
                session_record["manifest"] = manifest

            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, list):
                artifacts = []
                manifest["artifacts"] = artifacts

            artifacts.append(
                {
                    "timestamp": _now_iso(),
                    "command": command,
                    "path": result.get("saved_path")
                    or args.get("path")
                    or result.get("alias"),
                }
            )
            if repository is not None and identity_rows is not None:
                try:
                    run_id_raw = session_record.get("last_run_db_id")
                    if run_id_raw:
                        run_id = uuid.UUID(str(run_id_raw))
                        step_id = session_record.get("last_step_db_id")
                        step_uuid = uuid.UUID(str(step_id)) if step_id else None
                        await repository.store_artifact(
                            ArtifactCreateRequest(
                                tenant_id=identity_rows.tenant_id,
                                run_id=run_id,
                                step_id=step_uuid,
                                kind=ArtifactKind.FILE,
                                uri=str(
                                    result.get("saved_path")
                                    or args.get("path")
                                    or result.get("alias")
                                    or "memory://unknown"
                                ),
                                metadata_json={
                                    "command": command,
                                    "args": args,
                                },
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to persist artifact metadata: %s",
                        _sanitize_for_log(exc),
                    )

        await websocket.send_json(
            {
                "type": "command_result",
                "command": command,
                "result": result,
            }
        )
    except (ValueError, FileNotFoundError, KeyError) as exc:
        await websocket.send_json(
            {
                "type": "command_result",
                "command": command,
                "result": {"status": "error", "error": str(exc)},
            }
        )
    except Exception as exc:
        logger.error(
            "Command %s failed: %s",
            _sanitize_for_log(command),
            _sanitize_for_log(exc),
            exc_info=True,
            extra={
                "command": _sanitize_for_log(command),
                "error_type": type(exc).__name__,
            },
        )
        await websocket.send_json(
            {
                "type": "command_result",
                "command": command,
                "result": {
                    "status": "error",
                    "error": f"Internal error: {type(exc).__name__}: {exc}",
                },
            }
        )
