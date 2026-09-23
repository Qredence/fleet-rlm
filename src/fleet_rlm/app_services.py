"""Process resource inventory and typed services published to API routes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments import AttachmentLifecycle
from fleet_rlm.config.policy import ConfigPolicyService
from fleet_rlm.config.validation import CompositionError
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.ownership import RunCleanupSupervisor
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.lifecycle import SessionLifecycle
from fleet_rlm.turn_preparation import RunPreparation, TurnPreparationPlan, close_turn_preparation
from fleet_rlm.turn_settlement import RunLifecycle
from fleet_rlm.turns import TurnRuntime
from fleet_rlm.workspace.storage import WorkspaceVolumeGateway
from fleet_rlm.workspace.workspace import WorkspaceFileService


class SettlingRunStateStore(Protocol):
    """Run state-store surface needed by startup recovery."""

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary: ...


class DaytonaRuntimeSurface(Protocol):
    """Daytona runtime operations needed by startup recovery and prewarming."""

    async def fence_session(self, session_id: UUID, *, deadline: float | None = None) -> None: ...

    async def prewarm_session(
        self,
        session_id: UUID,
        *,
        user_id: UUID,
        workspace_id: UUID,
        deadline: float | None = None,
    ) -> bool: ...

    def schedule_prewarm(
        self,
        session_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
    ) -> asyncio.Task[None]:
        pass


class RuntimeProcessResources(Protocol):
    """Closeable process-scoped resources owned by one runtime composition."""

    @property
    def runtime(self) -> DaytonaRuntimeSurface: ...

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool | None: ...


class RuntimeInventoryError(RuntimeError):
    """Raised when a runtime inventory is incomplete or invalid."""


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


@dataclass(frozen=True, slots=True)
class RuntimeInventory:
    """Complete dynamic service graph installed for one application lifespan."""

    turn_runtime: TurnRuntime | None = None
    attachment_lifecycle: AttachmentLifecycle | None = None
    artifact_reader: ArtifactReader | None = None
    session_catalog: SessionCatalog | None = None
    run_lifecycle: RunLifecycle | None = None
    run_preparation: RunPreparation | TurnPreparationPlan | None = None
    run_cleanup_supervisor: RunCleanupSupervisor | None = None
    run_state_store: SettlingRunStateStore | None = None
    config_policy: ConfigPolicyService | None = None
    database: RuntimeDatabaseLifecycle = field(default_factory=RuntimeDatabaseLifecycle)
    run_environment_resources: RuntimeProcessResources | None = None
    model_bundle: RLMModelBundle | None = None
    workspace_volume_gateway: WorkspaceVolumeGateway | None = None
    workspace_file_service: WorkspaceFileService | None = None
    # The Daytona lifespan clears its own loop authority via clear_loop().
    bridge_dispatcher: SyncBridgeDispatcher | None = None
    # Best-effort post-readiness orphan sweep; cancelled at dispose. It must
    # never gate startup readiness, so it is tracked (not awaited) here.
    orphan_cleanup_task: asyncio.Task[None] | None = None
    # Best-effort post-readiness Memory promotion outbox sweep (P23); cancelled
    # at dispose like the orphan sweep and never readiness-gating.
    memory_outbox_task: asyncio.Task[None] | None = None
    # Optional explicit runner owner; kept after existing fields for positional
    # compatibility with provider-neutral inventory construction.
    runner: object | None = None
    session_lifecycle: SessionLifecycle | None = None

    _REQUIRED_ROUTE_FIELDS: ClassVar[tuple[str, ...]] = (
        "turn_runtime",
        "attachment_lifecycle",
        "artifact_reader",
        "session_catalog",
        "session_lifecycle",
        "run_lifecycle",
        "config_policy",
        "workspace_volume_gateway",
        "workspace_file_service",
    )

    def validate_complete(self) -> None:
        """Require every dynamic route-facing service before readiness is published."""
        missing = tuple(name for name in self._REQUIRED_ROUTE_FIELDS if getattr(self, name) is None)
        if missing:
            raise RuntimeInventoryError("runtime inventory missing required service(s): " + ", ".join(missing))

    @property
    def db_engine(self) -> AsyncEngine | None:
        return self.database.engine

    @property
    def daytona_runtime(self) -> DaytonaRuntimeSurface | None:
        if self.run_environment_resources is None:
            return None
        return self.run_environment_resources.runtime


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
    daytona_runtime: DaytonaRuntimeSurface | None

    @classmethod
    def from_inventory(cls, inventory: RuntimeInventory) -> RouteServices:
        inventory.validate_complete()
        return cls(
            turn_runtime=cast(TurnRuntime, inventory.turn_runtime),
            attachment_lifecycle=cast(AttachmentLifecycle, inventory.attachment_lifecycle),
            artifact_reader=cast(ArtifactReader, inventory.artifact_reader),
            session_catalog=cast(SessionCatalog, inventory.session_catalog),
            session_lifecycle=cast(SessionLifecycle, inventory.session_lifecycle),
            config_policy=cast(ConfigPolicyService, inventory.config_policy),
            workspace_volume_gateway=cast(WorkspaceVolumeGateway, inventory.workspace_volume_gateway),
            workspace_file_service=cast(WorkspaceFileService, inventory.workspace_file_service),
            daytona_runtime=inventory.daytona_runtime,
        )


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
    routes = RouteServices.from_inventory(inventory)
    app.state.runtime_inventory = inventory
    app.state.route_services = routes
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


async def close_preparation_services(preparation: RunPreparation | TurnPreparationPlan | None) -> bool:
    if isinstance(preparation, TurnPreparationPlan):
        return await close_turn_preparation(preparation)
    close = getattr(preparation, "aclose", None)
    return bool(await close()) if callable(close) else True


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

    cleanup = getattr(inventory, "run_cleanup_supervisor", None)
    if cleanup is not None:
        try:
            await cleanup.shutdown(drain_seconds=drain_seconds)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception as exc:
            errors.append(exc)

    runner = getattr(inventory, "runner", None)
    close_runner = getattr(runner, "aclose", None)
    if callable(close_runner):
        try:
            await close_runner(drain_seconds=drain_seconds)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception as exc:
            errors.append(exc)

    preparation = getattr(inventory, "run_preparation", None)
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
    "DaytonaRuntimeSurface",
    "RuntimeDatabaseLifecycle",
    "RuntimeInventory",
    "RuntimeInventoryError",
    "RuntimeProcessResources",
    "SettlingRunStateStore",
    "clear_runtime_inventory",
    "close_inventory_services",
    "get_runtime_inventory",
    "install_runtime_inventory",
    "no_provider_recovery_fence",
]
