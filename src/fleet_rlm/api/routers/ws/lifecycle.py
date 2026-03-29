"""Execution lifecycle helpers for websocket chat turns."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fleet_rlm.integrations.database import FleetRepository, RunStatus
from fleet_rlm.integrations.database.types import (
    IdentityUpsertResult,
    RunStepCreateRequest,
)

from ...execution import ExecutionEventType, ExecutionStep, ExecutionStepBuilder
from .execution_support import build_execution_event, map_execution_step_type
from .failures import PersistenceRequiredError
from .helpers import _sanitize_for_log

logger = logging.getLogger(__name__)


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
        return build_execution_event(
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
                        step_type=map_execution_step_type(step.type),
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
                    raise PersistenceRequiredError(
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
