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
from fleet_rlm.analytics.trace_context import runtime_distinct_id_context
from fleet_rlm.core.interpreter import ExecutionProfile
from fleet_rlm.db import FleetRepository
from fleet_rlm.db.models import (
    ArtifactKind,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
)
from fleet_rlm.db.types import (
    ArtifactCreateRequest,
    IdentityUpsertResult,
    MemoryItemCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)

from ..auth import AuthError
from ..deps import build_unauthenticated_identity, server_state, session_key
from ..utils import parse_model_identity, resolve_sandbox_provider
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


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    safe_session_id = _sanitize_id(session_id, "default-session")
    return (
        f"workspaces/{workspace_id}/users/{user_id}/memory/"
        f"react-session-{safe_session_id}.json"
    )


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
    cfg = server_state.config
    provider = server_state.auth_provider
    if provider is None:
        if cfg.auth_required:
            await websocket.accept()
            await websocket.send_json(
                _error_envelope(
                    code="auth_provider_missing", message="Auth provider missing"
                )
            )
            await websocket.close(code=1011)
            return None
        return build_unauthenticated_identity(cfg)

    try:
        return await provider.authenticate_websocket(websocket)
    except AuthError as exc:
        if cfg.auth_required:
            await websocket.accept()
            await websocket.send_json(
                _error_envelope(code="auth_failed", message=exc.message)
            )
            await websocket.close(code=1008)
            return None
        logger.debug("WS auth optional; continuing without auth: %s", exc.message)
        return build_unauthenticated_identity(cfg)


def _map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


def _get_execution_emitter():
    emitter = server_state.execution_event_emitter
    if emitter is not None:
        return emitter

    from ..execution_events import ExecutionEventEmitter

    cfg = server_state.config
    emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    server_state.execution_event_emitter = emitter
    return emitter


class PersistenceRequiredError(RuntimeError):
    """Raised when durable writes fail in strict-persistence mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _classify_stream_failure(exc: Exception) -> str:
    if isinstance(exc, PersistenceRequiredError):
        return exc.code

    lowered = str(exc).lower()
    if "planner lm not configured" in lowered:
        return "planner_missing"
    if "llm call timed out" in lowered or "timed out" in lowered and "llm" in lowered:
        return "llm_timeout"
    if "rate limit" in lowered or "429" in lowered:
        return "llm_rate_limited"
    if "sandbox" in lowered or "modal" in lowered:
        return "sandbox_unavailable"
    return "internal_error"


def _error_envelope(
    *, code: str, message: str, details: dict[str, Any] | None = None
) -> dict:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


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


class ExecutionLifecycleManager:
    """Encapsulates run lifecycle operations: DB persistence and event emission."""

    def __init__(
        self,
        *,
        run_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        execution_emitter,
        step_builder: ExecutionStepBuilder,
        repository: FleetRepository | None = None,
        identity_rows: IdentityUpsertResult | None = None,
        active_run_db_id: uuid.UUID | None = None,
        strict_persistence: bool = False,
        session_record: dict[str, Any] | None = None,
    ) -> None:
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.session_id = session_id
        self.execution_emitter = execution_emitter
        self.step_builder = step_builder
        self.repository = repository
        self.identity_rows = identity_rows
        self.active_run_db_id = active_run_db_id
        self.strict_persistence = strict_persistence
        self._session_record = session_record
        self._step_index = 0
        self._last_step_db_id: uuid.UUID | None = None
        self._persist_queue: asyncio.Queue[ExecutionStep | None] | None = None
        self._persist_worker_task: asyncio.Task[None] | None = None
        self._persistence_error: Exception | None = None
        self.run_completed = False

    def _build_event(
        self,
        event_type: ExecutionEventType,
        step: ExecutionStep | None = None,
    ) -> ExecutionEvent:
        return _build_execution_event(
            event_type=event_type,
            run_id=self.run_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            session_id=self.session_id,
            step=step,
        )

    @property
    def _can_persist(self) -> bool:
        return (
            self.repository is not None
            and self.identity_rows is not None
            and self.active_run_db_id is not None
        )

    def raise_if_persistence_error(self) -> None:
        if self.strict_persistence and self._persistence_error is not None:
            raise PersistenceRequiredError(
                "durable_state_write_failed",
                f"Durable state write failed: {self._persistence_error}",
            )

    async def _persist_worker(self) -> None:
        if not self._can_persist or self._persist_queue is None:
            return

        assert self.repository is not None
        assert self.identity_rows is not None
        assert self.active_run_db_id is not None

        while True:
            step = await self._persist_queue.get()
            if step is None:
                break
            self._step_index += 1
            try:
                persisted = await self.repository.append_step(
                    RunStepCreateRequest(
                        tenant_id=self.identity_rows.tenant_id,
                        run_id=self.active_run_db_id,
                        step_index=self._step_index,
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
                self._last_step_db_id = persisted.id
                if self._session_record is not None:
                    self._session_record["last_step_db_id"] = str(persisted.id)
            except Exception as exc:
                self._persistence_error = exc
                logger.warning(
                    "Failed to persist run step: %s",
                    _sanitize_for_log(exc),
                )
                if self.strict_persistence:
                    break

    async def _ensure_persist_worker(self) -> None:
        if not self._can_persist:
            return
        if self._persist_worker_task is not None:
            return
        self._persist_queue = asyncio.Queue(maxsize=512)
        self._persist_worker_task = asyncio.create_task(self._persist_worker())

    async def _stop_persist_worker(self) -> None:
        if self._persist_worker_task is None:
            return
        if self._persist_queue is not None:
            await self._persist_queue.put(None)
        try:
            await self._persist_worker_task
        except asyncio.CancelledError:
            pass
        self._persist_worker_task = None
        self._persist_queue = None

    async def emit_started(self) -> None:
        await self._ensure_persist_worker()
        await self.execution_emitter.emit(self._build_event("execution_started"))

    async def persist_step(self, step: ExecutionStep | None) -> None:
        if step is None or not self._can_persist:
            return
        await self._ensure_persist_worker()
        self.raise_if_persistence_error()
        if self._persist_queue is None:
            return
        try:
            self._persist_queue.put_nowait(step)
        except asyncio.QueueFull:
            if self.strict_persistence:
                raise PersistenceRequiredError(
                    "durable_state_backpressure",
                    "Execution step persistence queue is full",
                )
            await self._persist_queue.put(step)
        self.raise_if_persistence_error()

    async def emit_step(self, step: ExecutionStep) -> None:
        await self.execution_emitter.emit(
            self._build_event("execution_step", step=step)
        )

    async def complete_run(
        self,
        status: RunStatus,
        *,
        step: ExecutionStep | None = None,
        error_json: dict | None = None,
    ) -> None:
        if self.run_completed:
            return
        await self._stop_persist_worker()

        effective_status = status
        effective_error = dict(error_json or {})
        if self._persistence_error is not None:
            effective_error.setdefault(
                "durable_write_error", str(self._persistence_error)
            )
            effective_error.setdefault(
                "error_type", type(self._persistence_error).__name__
            )
            if self.strict_persistence:
                effective_status = RunStatus.FAILED
                effective_error.setdefault("code", "durable_state_write_failed")

        if self._can_persist:
            assert self.repository is not None
            assert self.identity_rows is not None
            assert self.active_run_db_id is not None
            try:
                await self.repository.update_run_status(
                    tenant_id=self.identity_rows.tenant_id,
                    run_id=self.active_run_db_id,
                    status=effective_status,
                    error_json=effective_error or None,
                )
            except Exception as exc:
                if self.strict_persistence:
                    raise PersistenceRequiredError(
                        "run_status_persist_failed",
                        f"Failed to persist run status: {exc}",
                    ) from exc
                logger.warning(
                    "Failed to persist run status: %s", _sanitize_for_log(exc)
                )
        await self.execution_emitter.emit(
            self._build_event("execution_completed", step=step)
        )
        self.run_completed = True


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

    analytics_distinct_id = (identity.user_claim or "").strip() or None
    with (
        runtime_distinct_id_context(analytics_distinct_id),
        dspy.context(lm=_planner_lm),
        agent_context as agent,
    ):
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
        lifecycle: ExecutionLifecycleManager | None = None
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
            previous_rev_raw = manifest.get("rev", 0)
            previous_rev_candidate = (
                previous_rev_raw
                if isinstance(previous_rev_raw, (int, float, str))
                else 0
            )
            try:
                previous_rev = int(previous_rev_candidate)
            except (TypeError, ValueError):
                previous_rev = 0
            manifest["rev"] = previous_rev + 1
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
                remote_manifest = _volume_load_manifest(agent, active_manifest_path)
                remote_rev_raw = remote_manifest.get("rev", 0)
                remote_rev_candidate = (
                    remote_rev_raw
                    if isinstance(remote_rev_raw, (int, float, str))
                    else 0
                )
                try:
                    remote_rev = int(remote_rev_candidate)
                except (TypeError, ValueError):
                    remote_rev = 0

                if remote_rev > previous_rev:
                    message = (
                        "Session manifest revision conflict detected "
                        f"(remote_rev={remote_rev}, local_rev={previous_rev})"
                    )
                    if persistence_required:
                        raise PersistenceRequiredError("manifest_conflict", message)
                    logger.warning(message)
                else:
                    saved_path = _volume_save_manifest(
                        agent, active_manifest_path, manifest
                    )
                    if saved_path is None:
                        message = (
                            "Failed to save session manifest to volume "
                            f"(path={active_manifest_path})"
                        )
                        if persistence_required:
                            raise PersistenceRequiredError(
                                "manifest_write_failed", message
                            )
                        logger.warning(message)

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
                    if persistence_required:
                        raise PersistenceRequiredError(
                            "memory_item_persist_failed",
                            f"Failed to persist memory item: {exc}",
                        ) from exc
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
                key = session_key(workspace_id, user_id, sess_id)
                manifest_path = _manifest_path(workspace_id, user_id, sess_id)

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
                        persistence_required=persistence_required,
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

                await persist_session_state(
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

                async def _emit_and_persist_repl_step(step_data: ExecutionStep) -> None:
                    try:
                        await lifecycle.emit_step(step_data)
                        await lifecycle.persist_step(step_data)
                    except Exception as exc:
                        logger.warning(
                            "Failed to emit/persist REPL execution step: %s",
                            _sanitize_for_log(exc),
                        )
                        lifecycle._persistence_error = exc

                def _interpreter_hook(payload: dict[str, Any]) -> None:
                    if lifecycle.run_completed:
                        return
                    repl_step = step_builder.from_interpreter_hook(payload)
                    if repl_step is None:
                        return
                    ws_loop.call_soon_threadsafe(
                        lambda step_data=repl_step: ws_loop.create_task(
                            _emit_and_persist_repl_step(step_data)
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
                        lifecycle.raise_if_persistence_error()
                        event_dict = {
                            "kind": event.kind,
                            "text": event.text,
                            "payload": event.payload,
                            "timestamp": event.timestamp.isoformat(),
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
                            await persist_session_state(include_volume_save=True)
                            await lifecycle.complete_run(RunStatus.COMPLETED, step=step)
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
                        interpreter.execution_event_callback = previous_execution_hook

        except WebSocketDisconnect:
            cancel_flag["cancelled"] = True
            try:
                await persist_session_state(include_volume_save=True)
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
                await persist_session_state(include_volume_save=True)
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


async def _handle_command(
    websocket: WebSocket,
    agent: "runners.RLMReActChatAgent",
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    *,
    repository: FleetRepository | None = None,
    identity_rows: IdentityUpsertResult | None = None,
    persistence_required: bool = False,
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
                    if persistence_required:
                        raise PersistenceRequiredError(
                            "artifact_persist_failed",
                            f"Failed to persist artifact metadata: {exc}",
                        ) from exc
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
