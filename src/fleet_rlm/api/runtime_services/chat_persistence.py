"""WebSocket chat run/session persistence orchestration for runtime services."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fleet_rlm.api.events import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionStep,
    ExecutionStepBuilder,
)
from fleet_rlm.api.server_utils import parse_model_identity, resolve_sandbox_provider
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log
from fleet_rlm.utils.time import now_iso
from fleet_rlm.integrations.database import (
    FleetRepository,
    MemoryKind,
    MemoryScope,
    MemorySource,
    RunStatus,
    RunStepType,
)
from fleet_rlm.integrations.database.types import (
    IdentityUpsertResult,
    MemoryItemCreateRequest,
    RunCreateRequest,
    RunStepCreateRequest,
)

from ..dependencies import ServerState

logger = logging.getLogger(__name__)

_EXECUTION_TO_RUN_STEP_TYPE: dict[str, RunStepType] = {
    "llm": RunStepType.LLM_CALL,
    "tool": RunStepType.TOOL_CALL,
    "repl": RunStepType.REPL_EXEC,
    "memory": RunStepType.MEMORY,
    "output": RunStepType.OUTPUT,
}


def _new_persistence_required_error(code: str, message: str) -> RuntimeError:
    from ..routers.ws.failures import PersistenceRequiredError

    return PersistenceRequiredError(code, message)


async def load_manifest_from_volume(agent: Any, path: str) -> dict[str, Any]:
    from ..routers.ws.manifest import load_manifest_from_volume as _load_manifest

    return await _load_manifest(agent, path)


async def save_manifest_to_volume(
    agent: Any,
    path: str,
    manifest: dict[str, Any],
) -> str | None:
    from ..routers.ws.manifest import save_manifest_to_volume as _save_manifest

    return await _save_manifest(agent, path, manifest)


def _build_execution_event(
    *,
    event_type: ExecutionEventType,
    run_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    step: ExecutionStep | None = None,
    summary: dict[str, Any] | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        type=event_type,
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        step=step,
        summary=summary,
    )


def _map_execution_step_type(step_type: str) -> RunStepType:
    return _EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


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
        self.run_completed = False

    def _build_event(
        self,
        event_type: ExecutionEventType,
        step: ExecutionStep | None = None,
        summary: dict[str, Any] | None = None,
    ) -> Any:
        return _build_execution_event(
            event_type=event_type,
            run_id=self.run_id,
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            session_id=self.session_id,
            step=step,
            summary=summary,
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
            raise _new_persistence_required_error(
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
                            step_type=_map_execution_step_type(batch_step.type),
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
                raise _new_persistence_required_error(
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
        summary: dict[str, Any] | None = None,
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
                    raise _new_persistence_required_error(
                        "run_status_persist_failed",
                        f"Failed to persist run status: {exc}",
                    ) from exc
                logger.warning(
                    "Failed to persist run status: %s", _sanitize_for_log(exc)
                )
        await self.execution_emitter.emit(
            self._build_event("execution_completed", step=step, summary=summary)
        )
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

    if (
        repository is not None
        and identity_rows is not None
        and identity_rows.user_id is not None
    ):
        model_provider, model_name = parse_model_identity(
            getattr(planner_lm, "model", None)
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
                        sandbox_provider or cfg.sandbox_provider
                    ),
                )
            )
            active_run_db_id = run_row.id
            if session_record is not None:
                session_record["last_run_db_id"] = str(run_row.id)
        except Exception as exc:
            if persistence_required:
                raise _new_persistence_required_error(
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
    previous_rev_candidate = (
        previous_rev_raw if isinstance(previous_rev_raw, (int, float, str)) else 0
    )
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
    state: ServerState,
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
        state.sessions[record_key] = session_record


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
            raise _new_persistence_required_error(
                "memory_item_persist_failed",
                f"Failed to persist memory item: {exc}",
            ) from exc
        logger.warning("Failed to persist memory item: %s", _sanitize_for_log(exc))


async def persist_session_state(
    *,
    state: ServerState,
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
        state=state,
        session_record=session_record,
        exported_state=exported_state,
    )

    if include_volume_save and active_manifest_path and interpreter is not None:
        remote_manifest = await load_manifest_from_volume(agent, active_manifest_path)
        remote_rev_raw = remote_manifest.get("rev", 0)
        remote_rev_candidate = (
            remote_rev_raw if isinstance(remote_rev_raw, (int, float, str)) else 0
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
                raise _new_persistence_required_error("manifest_conflict", message)
            logger.warning(message)
        else:
            saved_path = await save_manifest_to_volume(
                agent, active_manifest_path, manifest
            )
            if saved_path is None:
                message = (
                    "Failed to save session manifest to volume "
                    f"(path={active_manifest_path})"
                )
                if persistence_required:
                    raise _new_persistence_required_error(
                        "manifest_write_failed", message
                    )
                logger.warning(message)

    await persist_memory_item_if_needed(
        repository=repository,
        identity_rows=identity_rows,
        active_run_db_id=active_run_db_id,
        latest_user_message=latest_user_message,
        persistence_required=persistence_required,
    )


def build_local_persist_fn(
    *,
    state: ServerState,
    runtime: Any,
    agent: Any,
    interpreter: Any,
    session: Any,
):
    async def local_persist(
        *,
        include_volume_save: bool = True,
        latest_user_message: str = "",
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

    return local_persist


__all__ = [
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
