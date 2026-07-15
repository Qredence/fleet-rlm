"""Shared composition types and local inventory wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fleet_rlm.config import Settings
from fleet_rlm.rlm.budgets import RunBudget


class CompositionError(RuntimeError):
    """Raised when a runtime composition cannot be assembled."""


COMPOSITION_STATE_FIELDS = (
    "artifact_reader",
    "attachment_lifecycle",
    "rlm_model_bundle",
    "run_environment_resources",
    "session_catalog",
    "session_manager",
    "turn_coordinator",
    "turn_lifecycle",
    "turn_state_store",
    "workspace_volume_gateway",
    "workspace_volume_mirror",
)


@dataclass(slots=True)
class LocalCompositionHandles:
    """Process-owned adapters for Deno and private tests."""

    turn_coordinator: Any
    attachment_lifecycle: Any
    artifact_reader: Any
    workspace_volume_mirror: Any
    session_catalog: Any
    turn_lifecycle: Any


def host_roots(settings: Settings) -> tuple[str, str]:
    data_root = Path(settings.data_root)
    return str(data_root / "attachments"), str(data_root / "artifacts")


def run_budget(settings: Settings) -> RunBudget:
    """Project Settings onto the canonical per-Run Budget."""
    return RunBudget(
        max_iterations=settings.budget_max_iterations,
        max_llm_calls=settings.budget_max_llm_calls,
        max_output_chars=settings.budget_max_output_chars,
        max_wall_seconds=settings.budget_max_wall_seconds,
        max_sub_lm_concurrency=settings.budget_max_sub_lm_concurrency,
        max_tool_calls=settings.budget_max_tool_calls,
        max_skill_loads=settings.budget_max_skill_loads,
    )


def clear_composition_state(app: FastAPI) -> None:
    """Make every process-owned adapter unavailable after shutdown or rollback."""
    app.state.composition_ready = False
    for name in COMPOSITION_STATE_FIELDS:
        setattr(app.state, name, None)


def install_local_inventory(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None,
    attachment_lifecycle: Any,
    artifact_reader: Any,
    preparation: Any,
    rlm_factory: Any,
    workspace_volume_mirror: Any | None,
) -> LocalCompositionHandles:
    """Attach the shared in-memory/SQL inventory for one local runtime."""
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.persistence.repositories import (
        InMemorySessionCatalog,
        InMemoryTurnStateStore,
        SqlAlchemySessionCatalog,
        SqlAlchemyTurnStateStore,
    )
    from fleet_rlm.rlm.runner import RLMRunner

    if session_factory is None:
        turn_state = InMemoryTurnStateStore()
        session_catalog = InMemorySessionCatalog(turn_state)
    else:
        turn_state = SqlAlchemyTurnStateStore(
            session_factory,
            stale_after_seconds=settings.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
    lifecycle = TurnLifecycleModule(
        turn_state,
        max_artifact_bytes=settings.max_artifact_bytes,
        heartbeat_seconds=settings.run_heartbeat_seconds,
    )
    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=preparation,
        runner=RLMRunner(factory=rlm_factory),
    )
    handles = LocalCompositionHandles(
        turn_coordinator=coordinator,
        attachment_lifecycle=attachment_lifecycle,
        artifact_reader=artifact_reader,
        workspace_volume_mirror=workspace_volume_mirror,
        session_catalog=session_catalog,
        turn_lifecycle=lifecycle,
    )
    app.state.turn_coordinator = coordinator
    app.state.turn_lifecycle = lifecycle
    app.state.turn_state_store = turn_state
    app.state.session_catalog = session_catalog
    app.state.attachment_lifecycle = attachment_lifecycle
    app.state.artifact_reader = artifact_reader
    app.state.workspace_volume_mirror = workspace_volume_mirror
    app.state.composition_ready = True
    return handles
