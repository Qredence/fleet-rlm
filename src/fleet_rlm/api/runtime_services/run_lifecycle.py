"""Execution lifecycle management for runtime services.

Encapsulates run lifecycle operations: DB persistence and event emission.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from fleet_rlm.api.events import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionStep,
    ExecutionStepBuilder,
)
from fleet_rlm.api.runtime_services.common import (
    parse_model_identity,
    resolve_sandbox_provider,
)
from fleet_rlm.api.runtime_services.stream_failures import PersistenceRequiredError
from fleet_rlm.integrations.database import (
    FleetRepository,
    RunStatus,
    RunStepType,
)
from fleet_rlm.integrations.database.repository_chat import (
    RunCreateRequest,
    RunStepCreateRequest,
)
from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult
from fleet_rlm.utils.logging import sanitize_for_log as _sanitize_for_log

logger = logging.getLogger(__name__)

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


def map_execution_step_type(step_type: str) -> RunStepType:
    return EXECUTION_TO_RUN_STEP_TYPE.get(step_type, RunStepType.STATUS)


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
        if event_type == "execution_started" and self._session_record:
            db_sess_id = self._session_record.get("db_session_id")
            if db_sess_id:
                summary = dict(summary or {})
                summary["db_session_id"] = str(db_sess_id)

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
            logger.debug("Persist worker task was cancelled during shutdown.")
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

    async def _with_lifecycle_span(
        self,
        name: str,
        operation: Callable[[], Any],
        *,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        from fleet_rlm.integrations.observability.mlflow_context import (
            mlflow_child_span,
            set_mlflow_span_outputs,
        )

        manager = None
        span = None
        try:
            manager = mlflow_child_span(
                name,
                span_type="CHAIN",
                attributes={
                    "fleet_rlm.execution_origin": "execution_lifecycle",
                    "fleet_rlm.run_id": self.run_id,
                    **(attributes or {}),
                },
            )
            span = manager.__enter__()
        except Exception:
            logger.debug("MLflow lifecycle span skipped: %s", name, exc_info=True)
            manager = None

        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await result
            set_mlflow_span_outputs(span, {"status": "ok"})
            return result
        except BaseException as exc:
            if manager is not None:
                try:
                    manager.__exit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    logger.debug("MLflow lifecycle span exit skipped after error: %s", name, exc_info=True)
                finally:
                    manager = None
            raise
        finally:
            if manager is not None:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    logger.debug("MLflow lifecycle span exit skipped: %s", name, exc_info=True)

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
        await self._with_lifecycle_span(
            "fleet_rlm.lifecycle_persist_worker_drain",
            self._stop_persist_worker,
            attributes={"fleet_rlm.strict_persistence": str(self.strict_persistence).lower()},
        )

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
            repository = self.repository
            identity_rows = self.identity_rows
            active_run_db_id = self.active_run_db_id
            try:
                await self._with_lifecycle_span(
                    "fleet_rlm.lifecycle_update_run_status",
                    lambda: repository.update_run_status(
                        tenant_id=identity_rows.tenant_id,
                        run_id=active_run_db_id,
                        status=effective_status,
                        error_json=effective_error or None,
                    ),
                    attributes={
                        "fleet_rlm.run_status": str(effective_status.value),
                        "fleet_rlm.has_error_json": str(bool(effective_error)).lower(),
                    },
                )
            except Exception as exc:
                if self.strict_persistence:
                    raise PersistenceRequiredError(
                        "run_status_persist_failed",
                        f"Failed to persist run status: {exc}",
                    ) from exc
                logger.warning("Failed to persist run status: %s", _sanitize_for_log(exc))
        await self._with_lifecycle_span(
            "fleet_rlm.lifecycle_emit_completed",
            lambda: self.execution_emitter.emit(self._build_event("execution_completed", step=step, summary=summary)),
            attributes={"fleet_rlm.has_summary": str(bool(summary)).lower()},
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


__all__ = [
    "ExecutionLifecycleManager",
    "initialize_turn_lifecycle",
    "build_execution_event",
    "map_execution_step_type",
    "EXECUTION_TO_RUN_STEP_TYPE",
]
