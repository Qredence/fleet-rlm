"""Turn setup and command dispatch helpers for websocket chat."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import WebSocket

from fleet_rlm.infrastructure.database import FleetRepository
from fleet_rlm.infrastructure.database.models import RunStatus
from fleet_rlm.infrastructure.database.types import IdentityUpsertResult, RunCreateRequest

from ...execution import ExecutionStepBuilder
from ...utils import parse_model_identity, resolve_sandbox_provider
from .commands import _handle_command
from .helpers import _sanitize_for_log
from .lifecycle import ExecutionLifecycleManager, PersistenceRequiredError

logger = logging.getLogger(__name__)


async def handle_command_with_persist(
    *,
    websocket: WebSocket,
    agent: Any,
    payload: dict[str, Any],
    session_record: dict[str, Any] | None,
    repository: FleetRepository | None,
    identity_rows: IdentityUpsertResult | None,
    persistence_required: bool,
    local_persist: Callable[..., Awaitable[None]],
) -> None:
    """Dispatch command payload and persist session state afterward."""
    await _handle_command(
        websocket,
        agent,
        payload,
        session_record,
        repository=repository,
        identity_rows=identity_rows,
        persistence_required=persistence_required,
    )
    await local_persist(include_volume_save=True)


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

    if repository is not None and identity_rows is not None:
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
                raise PersistenceRequiredError(
                    "run_start_persist_failed",
                    f"Failed to persist run start: {exc}",
                ) from exc
            logger.warning("Failed to persist run start: %s", _sanitize_for_log(exc))

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
