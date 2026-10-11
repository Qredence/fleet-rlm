"""Process resource inventory and typed services published to API routes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher
    from fleet_rlm.daytona.runtime import DaytonaRuntime
    from fleet_rlm.observability.turn_capture import TurnCaptureStore

from fleet_rlm.config.policy import ConfigPolicyService
from fleet_rlm.config.validation import CompositionError
from fleet_rlm.observability.feedback import TraceFeedbackService
from fleet_rlm.observability.mlflow import MLflowRuntime
from fleet_rlm.persistence.repositories.run_state import ReconciliationSummary
from fleet_rlm.rlm.execution import RLMRunner
from fleet_rlm.rlm.ownership import RunCleanupSupervisor
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.lifecycle import SessionLifecycle
from fleet_rlm.sessions.models import SessionCatalog
from fleet_rlm.sessions.task import SessionTaskService
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.turns import TurnRuntime
from fleet_rlm.turns.preparation import RunPreparation
from fleet_rlm.workspace.artifacts import ArtifactReader
from fleet_rlm.workspace.attachments import AttachmentLifecycle
from fleet_rlm.workspace.storage import WorkspaceVolumeGateway
from fleet_rlm.workspace.workspace import WorkspaceFileService


@dataclass(slots=True)
class ApplicationComposition:
    """One application owns static services and retains its runtime through disposal."""

    skill_catalog: SkillCatalog
    mlflow_runtime: MLflowRuntime
    trace_feedback_service: TraceFeedbackService
    runtime: RuntimeInventory | None = None


def get_application_composition(app: FastAPI) -> ApplicationComposition:
    return app.state.composition


class SettlingRunStateStore(Protocol):
    """Run state-store surface needed by startup recovery."""

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary: ...


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseLifecycle:
    """Database handles created for one application lifespan."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    dispose_engine: bool = True
    # Bounded probe window for readiness(); a hung connection or statement
    # must degrade readiness, never wedge the probe open.
    _PROBE_TIMEOUT_SECONDS: ClassVar[float] = 5.0

    async def aclose(self) -> None:
        if self.dispose_engine and self.engine is not None:
            await self.engine.dispose()

    async def readiness(self) -> Literal["ok", "not_configured", "unreachable"]:
        """Probe the configured engine for health checks without raising.

        A probe failure degrades the verdict to "unreachable" instead of
        surfacing an error: any transport, driver, or server refusal, or a
        probe that exceeds its bounded window, is one closed verdict, and
        the caller translates it into its own public contract. The bound is
        cancellation-safe: CancelledError is BaseException and is never
        swallowed here.
        """
        if self.engine is None:
            return "not_configured"
        try:
            async with asyncio.timeout(self._PROBE_TIMEOUT_SECONDS):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except Exception:
            return "unreachable"
        return "ok"


@dataclass(slots=True)
class CompositionDisposal:
    """Application-owned deferred teardown, retained until resources settle."""

    task: asyncio.Task[bool] | None = None
    pending: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeInventory:
    """Route services and optional process resources for one application lifespan."""

    route_services: RouteServices
    disposal: CompositionDisposal = field(default_factory=CompositionDisposal)
    run_preparation: RunPreparation | None = None
    run_cleanup_supervisor: RunCleanupSupervisor | None = None
    run_state_store: SettlingRunStateStore | None = None
    database: RuntimeDatabaseLifecycle = field(default_factory=RuntimeDatabaseLifecycle)
    daytona_runtime_owner: DaytonaRuntime | None = None
    model_bundle: RLMModelBundle | None = None
    # The Daytona lifespan clears its own loop authority via clear_loop().
    bridge_dispatcher: SyncBridgeDispatcher | None = None
    # Best-effort post-readiness orphan sweep; cancelled at dispose. It must
    # never gate startup readiness, so it is tracked (not awaited) here.
    orphan_cleanup_task: asyncio.Task[None] | None = None
    # Best-effort post-readiness Memory promotion outbox sweep (P23); cancelled
    # at dispose like the orphan sweep and never readiness-gating.
    memory_outbox_task: asyncio.Task[None] | None = None
    runner: RLMRunner | None = None
    capture_store: TurnCaptureStore | None = None


@dataclass(frozen=True, slots=True)
class RouteServices:
    """Ready route dependencies, without provider ownership or teardown handles."""

    turn_runtime: TurnRuntime
    attachment_lifecycle: AttachmentLifecycle
    artifact_reader: ArtifactReader
    session_catalog: SessionCatalog
    session_lifecycle: SessionLifecycle
    config_policy: ConfigPolicyService
    workspace_volume_gateway: WorkspaceVolumeGateway
    workspace_file_service: WorkspaceFileService
    daytona_runtime: DaytonaRuntime | None
    session_task_service: SessionTaskService | None = None


def get_route_services(app: FastAPI) -> RouteServices | None:
    services = getattr(app.state, "route_services", None)
    return services if isinstance(services, RouteServices) else None


def get_runtime_inventory(app: FastAPI) -> RuntimeInventory | None:
    """Return the currently attached runtime inventory, if any."""
    inventory = getattr(app.state, "runtime_inventory", None)
    if isinstance(inventory, RuntimeInventory):
        return inventory
    return None


def install_runtime_inventory(app: FastAPI, inventory: RuntimeInventory) -> RuntimeInventory:
    """Publish a complete runtime graph and mark composition ready last."""
    app.state.runtime_inventory = inventory
    app.state.route_services = inventory.route_services
    app.state.composition_ready = True
    return inventory


def clear_runtime_inventory(app: FastAPI) -> RuntimeInventory | None:
    """Detach runtime services before the owning lifespan disposes resources."""
    app.state.composition_ready = False
    app.state.route_services = None
    detached = get_runtime_inventory(app)
    app.state.runtime_inventory = None
    return detached


async def no_provider_recovery_fence(_session_id: UUID) -> None:
    """Declare that deterministic compositions have no provider state to fence."""


@dataclass(frozen=True, slots=True)
class CloseServicesResult:
    """Outcome of the shared cleanup → runner → preparation close prefix.

    Live disposal uses ``preparation_settled`` and any recorded errors to decide
    whether provider resources may be torn down immediately or must remain
    retained for deferred ownership. Testing lifespans only need the errors.
    """

    errors: tuple[BaseException, ...] = ()
    preparation_settled: bool = True
    cancellation: asyncio.CancelledError | None = None

    @property
    def first_error(self) -> BaseException | None:
        return self.cancellation or (self.errors[0] if self.errors else None)


async def close_preparation_services(preparation: RunPreparation | None) -> bool:
    return await preparation.aclose() if preparation is not None else True


async def close_inventory_services(
    inventory: RuntimeInventory | None,
    *,
    drain_seconds: float = 30.0,
) -> CloseServicesResult:
    """Close cleanup supervisor, runner, then preparation in that order.

    This is the shared prefix for local and live lifespan disposal. Live-only
    retain gates, component teardown, and bridge fencing stay outside this
    helper so unsettled ownership cannot be released by the testing path.
    """
    if inventory is None:
        return CloseServicesResult()

    errors: list[Exception] = []
    cancellation: asyncio.CancelledError | None = None
    preparation_settled = True

    cleanup = inventory.run_cleanup_supervisor
    if cleanup is not None:
        try:
            await cleanup.shutdown(drain_seconds=drain_seconds)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception as exc:
            errors.append(exc)

    if inventory.runner is not None:
        try:
            await inventory.runner.aclose(drain_seconds=drain_seconds)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception as exc:
            errors.append(exc)

    preparation = inventory.run_preparation
    if preparation is not None:
        try:
            result = await close_preparation_services(preparation)
            if result is False:
                preparation_settled = False
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            preparation_settled = False
        except Exception as exc:
            preparation_settled = False
            errors.append(exc)

    return CloseServicesResult(
        errors=tuple(errors),
        preparation_settled=preparation_settled,
        cancellation=cancellation,
    )


__all__ = [
    "CloseServicesResult",
    "CompositionError",
    "RuntimeDatabaseLifecycle",
    "RuntimeInventory",
    "SettlingRunStateStore",
    "clear_runtime_inventory",
    "close_inventory_services",
    "get_runtime_inventory",
    "install_runtime_inventory",
    "no_provider_recovery_fence",
]
