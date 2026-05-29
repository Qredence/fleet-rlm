"""WebSocket chat run/session persistence orchestration for runtime services.

Consolidates lifecycle helpers, execution-event support, manifest I/O,
and session persistence into a single module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import shlex
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

from fastapi import WebSocketDisconnect

from fleet_rlm.api.events import (
    ExecutionEvent,
    ExecutionEventEmitter,
    ExecutionEventType,
    ExecutionStep,
    ExecutionStepBuilder,
)
from fleet_rlm.api.runtime_services.common import (
    parse_model_identity,
    resolve_sandbox_provider,
)
from fleet_rlm.api.runtime_services.session_paths import (
    session_conversation_path,
    session_scratchpad_path,
    session_workspace_link_path,
)
from fleet_rlm.integrations.database import (
    FleetRepository,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
)
from fleet_rlm.integrations.database.repository_chat import (
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.integrations.database.repository_memory import MemoryItemCreateRequest
from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile
from fleet_rlm.utils.identity import sanitize_id as _sanitize_id
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log
from fleet_rlm.utils.time import now_iso

from ..dependencies import ConfigDeps, DiagnosticsDeps, SessionCacheDeps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class PersistenceRequiredError(RuntimeError):
    """Raised when durable writes fail in strict-persistence mode."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def classify_stream_failure(exc: Exception) -> str:
    """Map runtime failures to stable websocket-facing error codes."""
    if isinstance(exc, PersistenceRequiredError):
        return exc.code

    lowered = str(exc).lower()
    if "planner lm not configured" in lowered:
        return "planner_missing"
    if "llm call timed out" in lowered or "timed out" in lowered and "llm" in lowered:
        return "llm_timeout"
    if "rate limit" in lowered or "429" in lowered:
        return "llm_rate_limited"
    if "sandbox" in lowered or "daytona" in lowered:
        return "sandbox_unavailable"
    return "internal_error"


# ---------------------------------------------------------------------------
# Execution-event support
# ---------------------------------------------------------------------------

EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


def build_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    sequence: int,
    step: ExecutionStep | None = None,
    summary: dict[str, Any] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        sequence=sequence,
        step=step,
        summary=summary,
    )


def get_execution_emitter(diagnostics: DiagnosticsDeps) -> ExecutionEventEmitter:
    emitter = diagnostics.events_event_emitter
    if emitter is not None:
        return emitter
    return emitter


def get_execution_emitter_with_config(diagnostics: DiagnosticsDeps, config_deps: ConfigDeps) -> ExecutionEventEmitter:
    emitter = diagnostics.events_event_emitter
    if emitter is not None:
        return emitter

    cfg = config_deps.config
    emitter = ExecutionEventEmitter(
        max_queue=cfg.ws_execution_max_queue,
        drop_policy=cfg.ws_execution_drop_policy,
    )
    diagnostics.events_event_emitter = emitter
    return emitter


def map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


# ---------------------------------------------------------------------------
# Startup status
# ---------------------------------------------------------------------------

EmitStartupEvent = Callable[[Any], Awaitable[None]]


def build_startup_status_event() -> Any:
    """Return the canonical delayed startup status event."""
    return SimpleNamespace(
        kind="turn_started",
        text="Preparing Daytona workspace...",
        payload={
            "phase": "startup",
            "runtime": {"runtime_mode": "daytona_pilot"},
        },
        timestamp=datetime.now(timezone.utc),
    )


async def emit_delayed_startup_status(
    *,
    delay_seconds: float,
    emit_event: EmitStartupEvent,
) -> None:
    """Emit the startup-status event after the configured first-frame delay."""
    await asyncio.sleep(delay_seconds)
    await emit_event(build_startup_status_event())


async def cancel_startup_status_task(task: asyncio.Task[None] | None) -> None:
    """Cancel the delayed startup task when startup completes first."""
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        _ = await task


# ---------------------------------------------------------------------------
# Task control
# ---------------------------------------------------------------------------


def should_reload_docs_path(last_docs_path: str | None, docs_path: str | None) -> bool:
    """Return True when a docs path is provided and differs from the last loaded path."""
    candidate = (docs_path or "").strip()
    if not candidate:
        return False
    return candidate != (last_docs_path or "")


def enqueue_latest_nonblocking(
    queue: asyncio.Queue[Any],
    item: Any,
) -> bool:
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


async def cancel_task(task: asyncio.Task[object] | None) -> None:
    """Cancel an in-flight task and swallow expected shutdown exceptions."""
    if task is None or task.done():
        return

    task.cancel()
    outcomes = await asyncio.gather(task, return_exceptions=True)
    if not outcomes:
        return

    outcome = outcomes[0]
    if isinstance(outcome, (asyncio.CancelledError, WebSocketDisconnect)):
        return
    if isinstance(outcome, BaseException):
        raise outcome


# ---------------------------------------------------------------------------
# Loop exit
# ---------------------------------------------------------------------------


async def handle_chat_disconnect(
    *,
    pending_receive_task: asyncio.Task[object] | None,
    stream_task: asyncio.Task[str | None] | None,
    cancel_flag: dict[str, bool],
    local_persist: Callable[..., Awaitable[None]],
    lifecycle: ExecutionLifecycleManager | None,
) -> None:
    """Cleanly stop the active websocket loop after a client disconnect."""
    cancel_flag["cancelled"] = True
    await cancel_task(pending_receive_task)
    await cancel_task(stream_task)
    try:
        await local_persist(
            include_volume_save=True,
            allow_volume_session_create=False,
            release_idle_session=True,
        )
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


# ---------------------------------------------------------------------------
# Worker request
# ---------------------------------------------------------------------------


def build_workspace_task_request(
    *,
    agent: Any,
    prepared_turn: Any,
    cancel_check: Callable[[], bool],
) -> Any:
    """Build the worker request for one websocket message turn."""
    return SimpleNamespace(
        agent=agent,
        message=prepared_turn.message,
        execution_mode=prepared_turn.execution_mode,
        trace=prepared_turn.trace,
        docs_path=prepared_turn.docs_path,
        repo_url=prepared_turn.repo_url,
        repo_ref=prepared_turn.repo_ref,
        context_paths=(list(prepared_turn.context_paths) if prepared_turn.context_paths is not None else None),
        batch_concurrency=prepared_turn.batch_concurrency,
        workspace_id=prepared_turn.workspace_id,
        cancel_check=cancel_check,
        prepare=prepared_turn.prepare_worker,
    )


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------


def _is_final_output(result: Any) -> bool:
    from dspy.primitives import FinalOutput

    return isinstance(result, FinalOutput)


def _manifest_path(workspace_id: str, user_id: str, session_id: str) -> str:
    _ = workspace_id, user_id
    conversation_path = session_conversation_path(session_id)
    if conversation_path is not None:
        return conversation_path
    safe_session_id = _sanitize_id(session_id, "default-session")
    return f"meta/workspaces/{workspace_id}/users/{user_id}/react-session-{safe_session_id}.json"


def _get_existing_daytona_session(agent: Any) -> Any | None:
    interpreter = getattr(agent, "interpreter", None)
    workspace = getattr(interpreter, "_workspace", None)
    if workspace is None:
        return None
    return getattr(workspace, "_session", None)


async def _aget_daytona_session(agent: Any, *, allow_create: bool = True) -> Any | None:
    try:
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
    except ImportError:
        return None

    interpreter = getattr(agent, "interpreter", None)
    if not isinstance(interpreter, DaytonaInterpreter):
        return None
    if not allow_create:
        return _get_existing_daytona_session(agent)
    aget_session = getattr(interpreter, "aget_session", None)
    if aget_session is None or not callable(aget_session):
        return None
    return await aget_session()


async def release_idle_daytona_session(agent: Any) -> None:
    """Best-effort release of an already-created Daytona sandbox session."""
    interpreter = getattr(agent, "interpreter", None)
    if interpreter is None:
        return
    if _get_existing_daytona_session(agent) is None:
        return
    release_idle = getattr(interpreter, "arelease_idle_session", None)
    if callable(release_idle):
        try:
            await release_idle()
        except Exception:
            logger.warning("Failed to release idle Daytona session", exc_info=True)


def _persistent_storage_path(interpreter: Any, path: str) -> str:
    raw_root = str(getattr(interpreter, "volume_mount_path", "/data") or "/data")
    mount_root = posixpath.normpath(raw_root)
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        resolved = posixpath.normpath(str(candidate))
    else:
        resolved = posixpath.normpath(str(PurePosixPath(mount_root) / candidate))
    if not resolved.startswith(mount_root + "/") and resolved != mount_root:
        raise ValueError(f"Path {path!r} resolves outside volume mount path.")
    return resolved


def _session_workspace_target(daytona_session: Any, interpreter: Any) -> str:
    return str(
        getattr(daytona_session, "workspace_path", None)
        or getattr(interpreter, "workspace_path", None)
        or getattr(interpreter, "repo_path", None)
        or ""
    ).strip()


def _ensure_session_layout_command(*, scratchpad_path: str, workspace_link_path: str, workspace_target: str) -> str:
    return " ".join(
        [
            "mkdir",
            "-p",
            shlex.quote(scratchpad_path),
            "&&",
            "rm",
            "-rf",
            shlex.quote(workspace_link_path),
            "&&",
            "ln",
            "-s",
            shlex.quote(workspace_target),
            shlex.quote(workspace_link_path),
        ]
    )


async def ensure_session_volume_layout(
    agent: Any,
    session_id: str,
    *,
    allow_session_create: bool = True,
) -> dict[str, str]:
    """Ensure Phase 1 per-session scratchpad and workspace mapping exist on the volume."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    scratchpad_path = session_scratchpad_path(session_id)
    workspace_link_path = session_workspace_link_path(session_id)
    if scratchpad_path is None or workspace_link_path is None:
        return {}
    storage_scratchpad_path = _persistent_storage_path(interpreter, scratchpad_path)
    storage_workspace_link_path = _persistent_storage_path(interpreter, workspace_link_path)
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
    if daytona_session is None and not allow_session_create:
        return {
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
        }
    workspace_target = _session_workspace_target(daytona_session, interpreter)
    if not workspace_target:
        return {
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
        }
    if daytona_session is not None:
        process = getattr(getattr(daytona_session, "sandbox", None), "process", None)
        exec_command = getattr(process, "exec", None)
        if callable(exec_command):
            try:
                exec_command(
                    _ensure_session_layout_command(
                        scratchpad_path=storage_scratchpad_path,
                        workspace_link_path=storage_workspace_link_path,
                        workspace_target=workspace_target,
                    )
                )
                return {
                    "scratchpad_path": storage_scratchpad_path,
                    "workspace_link_path": storage_workspace_link_path,
                }
            except Exception as exc:
                logger.warning(
                    "ensure_session_volume_layout: Daytona exec_command failed, falling back to interpreter aexecute: %s",
                    exc,
                )
    await interpreter.aexecute(
        "\n".join(
            [
                "import os",
                "os.makedirs(scratchpad_path, exist_ok=True)",
                "if os.path.isdir(workspace_target):",
                "    if os.path.lexists(workspace_link_path):",
                "        if os.path.isdir(workspace_link_path) and not os.path.islink(workspace_link_path):",
                "            import shutil",
                "            shutil.rmtree(workspace_link_path)",
                "        else:",
                "            os.unlink(workspace_link_path)",
                "    os.symlink(workspace_target, workspace_link_path)",
                "else:",
                "    import warnings",
                "    warnings.warn(f'Workspace target {workspace_target} does not exist, skipping symlink creation')",
                "SUBMIT(scratchpad_path=scratchpad_path, workspace_link_path=workspace_link_path)",
            ]
        ),
        variables={
            "scratchpad_path": storage_scratchpad_path,
            "workspace_link_path": storage_workspace_link_path,
            "workspace_target": workspace_target,
        },
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    return {
        "scratchpad_path": storage_scratchpad_path,
        "workspace_link_path": storage_workspace_link_path,
    }


def _parse_manifest_text(text: str) -> dict[str, Any]:
    if not text or text.startswith("[file not found:") or text.startswith("[error:"):
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def load_manifest_from_volume(
    agent: Any,
    path: str,
    fallback_paths: list[str] | None = None,
    *,
    allow_session_create: bool = True,
) -> dict[str, Any]:
    """Best-effort manifest load from interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return {}
    candidate_paths = [path, *(fallback_paths or [])]
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
    if daytona_session is not None:
        for candidate_path in candidate_paths:
            storage_path = _persistent_storage_path(interpreter, candidate_path)
            try:
                text = await daytona_session.aread_file(storage_path)
            except Exception:
                logger.debug(
                    "manifest_load_daytona_read_error",
                    extra={"path": storage_path},
                    exc_info=True,
                )
                continue
            parsed = _parse_manifest_text(text)
            if parsed:
                return parsed
        return {}
    if not allow_session_create:
        return {}
    for candidate_path in candidate_paths:
        result = await interpreter.aexecute(
            "text = load_from_volume(path)\nSUBMIT(text=text)",
            variables={"path": candidate_path},
            execution_profile=ExecutionProfile.MAINTENANCE,
        )
        if not _is_final_output(result):
            continue
        output = getattr(result, "output", None)
        output = output if isinstance(output, dict) else {}
        parsed = _parse_manifest_text(str(output.get("text", "")))
        if parsed:
            return parsed
    return {}


async def save_manifest_to_volume(
    agent: Any,
    path: str,
    manifest: dict[str, Any],
    *,
    allow_session_create: bool = True,
) -> str | None:
    """Best-effort manifest save to interpreter volume storage."""
    interpreter = agent.interpreter
    if interpreter is None:
        return None
    payload = json.dumps(manifest, ensure_ascii=False, default=str)
    daytona_session = await _aget_daytona_session(agent, allow_create=allow_session_create)
    if daytona_session is not None:
        storage_path = _persistent_storage_path(interpreter, path)
        try:
            return await daytona_session.awrite_file(storage_path, payload)
        except Exception:
            logger.warning(
                "manifest_save_daytona_write_error",
                extra={"path": storage_path},
                exc_info=True,
            )
            return None
    if not allow_session_create:
        return None
    result = await interpreter.aexecute(
        "saved_path = save_to_volume(path, payload)\nSUBMIT(saved_path=saved_path)",
        variables={"path": path, "payload": payload},
        execution_profile=ExecutionProfile.MAINTENANCE,
    )
    if not _is_final_output(result):
        return None
    output = getattr(result, "output", None)
    output = output if isinstance(output, dict) else {}
    saved_path = str(output.get("saved_path", ""))
    if saved_path.startswith("["):
        return None
    return saved_path or None


# ---------------------------------------------------------------------------
# Execution lifecycle manager
# ---------------------------------------------------------------------------


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
        active_run_db_id: Any = None,
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
        self._last_step_db_id: Any = None
        self._persist_queue: asyncio.Queue[ExecutionStep | None] | None = None
        self._persist_worker_task: asyncio.Task[None] | None = None
        self._persistence_error: Exception | None = None
        self._event_sequence = 0
        self.run_completed = False

    def _build_event(
        self,
        event_type: ExecutionEventType,
        step: ExecutionStep | None = None,
        summary: dict[str, Any] | None = None,
    ) -> Any:
        self._event_sequence += 1
        return build_execution_event(
            event_type=event_type,
            run_id=self.run_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            session_id=self.session_id,
            sequence=self._event_sequence,
            step=step,
            summary=summary,
        )

    @property
    def _can_persist(self) -> bool:
        return self.repository is not None and self.identity_rows is not None and self.active_run_db_id is not None

    def raise_if_persistence_error(self) -> None:
        if self.strict_persistence and self._persistence_error is not None:
            raise PersistenceRequiredError(
                "durable_state_write_failed",
                f"Durable state write failed: {self._persistence_error}",
            )

    def record_persistence_error(self, exc: Exception) -> None:
        self._persistence_error = exc

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

            # Coalesce additional steps already in the queue to reduce
            # per-item overhead and database round-trips.
            batch: list[ExecutionStep] = [step]
            shutdown_requested = False
            while len(batch) < 32:
                try:
                    extra = self._persist_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if extra is None:
                    shutdown_requested = True
                    break
                batch.append(extra)

            for batch_step in batch:
                self._step_index += 1
                try:
                    persisted = await self.repository.append_step(
                        RunStepCreateRequest(
                            tenant_id=self.identity_rows.tenant_id,
                            run_id=self.active_run_db_id,
                            step_index=self._step_index,
                            step_type=map_execution_step_type(batch_step.type),
                            input_json=batch_step.input
                            if isinstance(batch_step.input, dict)
                            else {"value": batch_step.input}
                            if batch_step.input is not None
                            else None,
                            output_json=batch_step.output
                            if isinstance(batch_step.output, dict)
                            else {"value": batch_step.output}
                            if batch_step.output is not None
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
            if self.strict_persistence and self._persistence_error is not None:
                break
            if shutdown_requested:
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
        await self.execution_emitter.emit(self._build_event("execution_step", step=step))

    async def complete_run(
        self,
        status: RunStatus,
        *,
        step: ExecutionStep | None = None,
        error_json: dict | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        if self.run_completed:
            return
        await self._stop_persist_worker()

        effective_status = status
        effective_error = dict(error_json or {})
        if self._persistence_error is not None:
            effective_error.setdefault("durable_write_error", str(self._persistence_error))
            effective_error.setdefault("error_type", type(self._persistence_error).__name__)
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
                logger.warning("Failed to persist run status: %s", _sanitize_for_log(exc))
        await self.execution_emitter.emit(self._build_event("execution_completed", step=step, summary=summary))
        self.run_completed = True


async def initialize_turn_lifecycle(
    *,
    planner_lm: Any,
    cfg: Any,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    execution_emitter: Any,
    workspace_id: str,
    user_id: str,
    sess_id: str,
    turn_index: int,
    session_record: dict[str, Any] | None,
    sandbox_provider: str | None = None,
) -> tuple[ExecutionLifecycleManager, ExecutionStepBuilder, str, Any]:
    """Create step builder and lifecycle manager for a single message turn."""
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

    if repository is not None and identity_rows is not None and identity_rows.user_id is not None:
        model_provider, model_name = parse_model_identity(getattr(planner_lm, "model", None))
        try:
            run_row = await repository.create_run(
                RunCreateRequest(
                    tenant_id=identity_rows.tenant_id,
                    created_by_user_id=identity_rows.user_id,
                    external_run_id=run_id,
                    status=RunStatus.RUNNING,
                    model_provider=model_provider,
                    model_name=model_name,
                    sandbox_provider=resolve_sandbox_provider(sandbox_provider or cfg.sandbox_provider),
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
            logger.warning("Failed to persist run start: %s", _sanitize_for_log(exc))
    elif repository is not None and identity_rows is not None:
        logger.info(
            "runtime_run_persistence_skipped_missing_user",
            extra={
                "run_id": run_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "session_id": sess_id,
                "tenant_id": str(identity_rows.tenant_id),
                "code": "identity_missing_user",
            },
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
    return lifecycle, step_builder, run_id, active_run_db_id


def ensure_manifest_shape(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize mutable manifest structure and expected keys."""
    if not isinstance(manifest.get("logs"), list):
        manifest["logs"] = []
    if not isinstance(manifest.get("memory"), list):
        manifest["memory"] = []
    if not isinstance(manifest.get("generated_docs"), list):
        manifest["generated_docs"] = []
    if not isinstance(manifest.get("artifacts"), list):
        manifest["artifacts"] = []
    if not isinstance(manifest.get("metadata"), dict):
        manifest["metadata"] = {}
    return manifest


def update_manifest_from_exported_state(
    *,
    manifest: dict[str, Any],
    exported_state: dict[str, Any],
    latest_user_message: str,
) -> tuple[int, int]:
    """Update manifest with latest state snapshot and optional user message entry."""
    ensure_manifest_shape(manifest)

    logs = manifest["logs"]
    memory = manifest["memory"]
    generated_docs = manifest["generated_docs"]
    artifacts = manifest["artifacts"]
    metadata = manifest["metadata"]

    if latest_user_message:
        logs.append(
            {
                "timestamp": now_iso(),
                "user_message": latest_user_message,
                "history_turns": len(exported_state.get("history", [])),
            }
        )
        memory.append(
            {
                "timestamp": now_iso(),
                "content": latest_user_message[:400],
            }
        )

    generated_docs[:] = sorted(list(exported_state.get("documents", {}).keys()))

    previous_rev_raw = manifest.get("rev", 0)
    previous_rev_candidate = previous_rev_raw if isinstance(previous_rev_raw, (int, float, str)) else 0
    try:
        previous_rev = int(previous_rev_candidate)
    except (TypeError, ValueError):
        previous_rev = 0

    next_rev = previous_rev + 1
    manifest["rev"] = next_rev
    metadata["updated_at"] = now_iso()
    metadata["history_turns"] = len(exported_state.get("history", []))
    metadata["document_count"] = len(exported_state.get("documents", {}))
    metadata["artifact_count"] = len(artifacts)
    manifest["state"] = exported_state
    return previous_rev, next_rev


def sync_session_record_state(
    *,
    session_cache: SessionCacheDeps,
    session_record: dict[str, Any],
    exported_state: dict[str, Any],
) -> None:
    """Propagate exported state into session record and state cache."""
    session_data = session_record.get("session")
    if not isinstance(session_data, dict):
        session_data = {}
        session_record["session"] = session_data
    session_data["state"] = exported_state
    session_data["session_id"] = session_record.get("session_id")

    record_key = session_record.get("key")
    if isinstance(record_key, str):
        session_cache.sessions[record_key] = session_record


async def persist_memory_item_if_needed(
    *,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    active_run_db_id: Any,
    latest_user_message: str,
    persistence_required: bool,
) -> None:
    """Persist a user-input memory item when repository context is available."""
    if not latest_user_message or repository is None or identity_rows is None:
        return
    try:
        await repository.store_memory_item(
            MemoryItemCreateRequest(
                tenant_id=identity_rows.tenant_id,
                workspace_id=identity_rows.workspace_id,
                user_id=identity_rows.user_id,
                run_id=active_run_db_id,
                scope=MemoryScope.RUN if active_run_db_id is not None else MemoryScope.USER,
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
        logger.warning("Failed to persist memory item: %s", _sanitize_for_log(exc))


async def _persist_manifest_to_local_store(
    *,
    persistence: Any,
    sess_id: str,
    manifest: dict[str, Any],
) -> None:
    """Write the manifest into LocalStore/FleetRepository session metadata.

    Used as a fallback when no Daytona volume is available (interpreter=None) so
    that session state survives process restarts between WebSocket connections.
    """
    if persistence is None:
        return
    update_fn = getattr(persistence, "update_chat_session", None)
    if not callable(update_fn):
        return
    try:
        import inspect

        sig = inspect.signature(update_fn)
        # LocalStore.update_chat_session requires tenant_id + session_id UUIDs; the
        # async FleetRepository variant has the same shape.  Both accept metadata_json.
        # We store under the raw external_session_id key so the restore helper can
        # locate it without a UUID round-trip.
        params = set(sig.parameters)
        if "external_session_id" in params:
            await update_fn(external_session_id=sess_id, metadata_json={"_manifest_state": manifest})
        else:
            # Async path: skip – we cannot derive the UUID here without identity_rows.
            pass
    except Exception:
        logger.debug("Best-effort manifest persist to local store failed", exc_info=True)


async def _restore_manifest_from_local_store(
    *,
    persistence: Any,
    sess_id: str,
) -> dict[str, Any]:
    """Read a previously persisted manifest from LocalStore session metadata.

    Returns an empty dict when nothing is found or an error occurs.
    """
    if persistence is None:
        return {}
    get_fn = getattr(persistence, "get_chat_session_by_external_id", None)
    if not callable(get_fn):
        return {}
    try:
        row = await get_fn(external_session_id=sess_id)
        if row is None:
            return {}
        metadata = getattr(row, "metadata_json", None)
        if not isinstance(metadata, dict):
            return {}
        manifest = metadata.get("_manifest_state")
        return manifest if isinstance(manifest, dict) else {}
    except Exception:
        logger.debug("Best-effort manifest restore from local store failed", exc_info=True)
        return {}


async def persist_session_state(
    *,
    session_cache: SessionCacheDeps,
    agent: Any,
    session_record: dict[str, Any] | None,
    active_manifest_path: str | None,
    active_run_db_id: uuid.UUID | None,
    interpreter: Any | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    include_volume_save: bool = True,
    latest_user_message: str = "",
    persistence: Any = None,
    allow_volume_session_create: bool = True,
    release_idle_session: bool = False,
) -> None:
    """Persist current session state and optionally release the live Daytona sandbox."""
    try:
        await _persist_session_state_impl(
            session_cache=session_cache,
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
            persistence=persistence,
            allow_volume_session_create=allow_volume_session_create,
        )
    finally:
        if release_idle_session:
            await release_idle_daytona_session(agent)


async def _persist_session_state_impl(
    *,
    session_cache: SessionCacheDeps,
    agent: Any,
    session_record: dict[str, Any] | None,
    active_manifest_path: str | None,
    active_run_db_id: uuid.UUID | None,
    interpreter: Any | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    include_volume_save: bool = True,
    latest_user_message: str = "",
    persistence: Any = None,
    allow_volume_session_create: bool = True,
) -> None:
    """Persist current session state to in-memory cache, volume, and DB."""
    if session_record is None:
        return
    exported_state = agent.export_session_state()
    manifest = session_record.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
        session_record["manifest"] = manifest

    ensure_manifest_shape(manifest)
    previous_rev, _next_rev = update_manifest_from_exported_state(
        manifest=manifest,
        exported_state=exported_state,
        latest_user_message=latest_user_message,
    )
    sync_session_record_state(
        session_cache=session_cache,
        session_record=session_record,
        exported_state=exported_state,
    )

    if include_volume_save and active_manifest_path and interpreter is not None:
        existing_session = None
        if not allow_volume_session_create:
            existing_session = await _aget_daytona_session(agent, allow_create=False)
        if allow_volume_session_create or existing_session is not None:
            remote_manifest = await load_manifest_from_volume(
                agent,
                active_manifest_path,
                allow_session_create=allow_volume_session_create,
            )
            remote_rev_raw = remote_manifest.get("rev", 0)
            remote_rev_candidate = remote_rev_raw if isinstance(remote_rev_raw, (int, float, str)) else 0
            try:
                remote_rev = int(remote_rev_candidate)
            except (TypeError, ValueError):
                remote_rev = 0

            if remote_rev > previous_rev:
                message = (
                    f"Session manifest revision conflict detected (remote_rev={remote_rev}, local_rev={previous_rev})"
                )
                if persistence_required:
                    raise PersistenceRequiredError("manifest_conflict", message)
                logger.warning(message)
            else:
                saved_path = await save_manifest_to_volume(
                    agent,
                    active_manifest_path,
                    manifest,
                    allow_session_create=allow_volume_session_create,
                )
                if saved_path is None:
                    message = f"Failed to save session manifest to volume (path={active_manifest_path})"
                    if persistence_required:
                        raise PersistenceRequiredError("manifest_write_failed", message)
                    logger.warning(message)
        else:
            logger.debug(
                "Skipping Daytona volume persistence because cleanup has no active session (path=%s)",
                active_manifest_path,
            )
    elif include_volume_save and interpreter is None and persistence is not None:
        # No Daytona volume available — fall back to local store so the manifest
        # survives process restarts between WebSocket connections.
        sess_id = str(session_record.get("session_id") or "")
        if sess_id:
            await _persist_manifest_to_local_store(
                persistence=persistence,
                sess_id=sess_id,
                manifest=manifest,
            )

    await persist_memory_item_if_needed(
        repository=repository,
        identity_rows=identity_rows,
        active_run_db_id=active_run_db_id,
        latest_user_message=latest_user_message,
        persistence_required=persistence_required,
    )


def build_local_persist_fn(
    *,
    session_cache: SessionCacheDeps,
    runtime: Any,
    agent: Any,
    interpreter: Any,
    session: Any,
):
    async def local_persist(
        *,
        include_volume_save: bool = True,
        latest_user_message: str = "",
        allow_volume_session_create: bool = True,
        release_idle_session: bool = False,
    ) -> None:
        try:
            await persist_session_state(
                session_cache=session_cache,
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
                persistence=runtime.persistence,
                allow_volume_session_create=allow_volume_session_create,
                release_idle_session=False,
            )
        finally:
            if release_idle_session:
                await release_idle_daytona_session(agent)

    return local_persist


__all__ = [
    "PersistenceRequiredError",
    "classify_stream_failure",
    "EXECUTION_TO_RUN_STEP_TYPE",
    "build_execution_event",
    "get_execution_emitter",
    "get_execution_emitter_with_config",
    "map_execution_step_type",
    "build_startup_status_event",
    "emit_delayed_startup_status",
    "cancel_startup_status_task",
    "should_reload_docs_path",
    "enqueue_latest_nonblocking",
    "cancel_task",
    "handle_chat_disconnect",
    "build_workspace_task_request",
    "load_manifest_from_volume",
    "save_manifest_to_volume",
    "release_idle_daytona_session",
    "_persist_manifest_to_local_store",
    "_restore_manifest_from_local_store",
    "_manifest_path",
    "_aget_daytona_session",
    "_persistent_storage_path",
    "_is_final_output",
    "ExecutionLifecycleManager",
    "build_local_persist_fn",
    "ensure_manifest_shape",
    "initialize_turn_lifecycle",
    "now_iso",
    "persist_memory_item_if_needed",
    "persist_session_state",
    "sync_session_record_state",
    "update_manifest_from_exported_state",
]
