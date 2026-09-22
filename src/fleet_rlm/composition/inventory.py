"""Typed runtime inventory publication for FastAPI lifespan composition.

Why these seams are Protocols rather than attributes: the inventory is
provider-neutral. `SettlingRunStateStore`, `RuntimeSessionManager`, and
`RuntimeProcessResources` let `composition/inventory.py` name exactly the
surfaces startup recovery needs without importing `daytona/` (which owns the
SDK boundary), and they let the private testing composition substitute
deterministic, credential-free implementations for every provider-backed
participant. Folding them into concrete classes would force composition to
import provider modules and re-couple the test suite to Daytona.
"""

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

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments import AttachmentLifecycle
from fleet_rlm.chat.preparation import RunPreparation
from fleet_rlm.chat.run_lifecycle import RunLifecycle
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.config.policy import ConfigPolicyService
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.sessions.lifecycle import SessionLifecycle
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


class RuntimeSessionManager(Protocol):
    """Provider session manager surface needed by startup recovery."""

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
    def session_manager(self) -> RuntimeSessionManager: ...

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool | None: ...


class CompositionError(RuntimeError):
    """Raised when a runtime composition cannot be assembled."""


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
    run_preparation: RunPreparation | None = None
    run_cleanup_supervisor: RunCleanupSupervisor | None = None
    run_state_store: SettlingRunStateStore | None = None
    config_policy: ConfigPolicyService | None = None
    database: RuntimeDatabaseLifecycle = field(default_factory=RuntimeDatabaseLifecycle)
    run_environment_resources: RuntimeProcessResources | None = None
    model_bundle: RLMModelBundle | None = None
    workspace_volume_gateway: WorkspaceVolumeGateway | None = None
    workspace_file_service: WorkspaceFileService | None = None
    # Composition-owned Daytona sync-bridge dispatcher (QRE-154); disposed
    # compositions clear their own loop authority via clear_loop().
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

    def require_turn_runtime(self) -> TurnRuntime:
        if self.turn_runtime is None:
            raise RuntimeInventoryError("runtime inventory missing required service: turn_runtime")
        return self.turn_runtime

    def require_attachment_lifecycle(self) -> AttachmentLifecycle:
        if self.attachment_lifecycle is None:
            raise RuntimeInventoryError("runtime inventory missing required service: attachment_lifecycle")
        return self.attachment_lifecycle

    def require_artifact_reader(self) -> ArtifactReader:
        if self.artifact_reader is None:
            raise RuntimeInventoryError("runtime inventory missing required service: artifact_reader")
        return self.artifact_reader

    def require_session_catalog(self) -> SessionCatalog:
        if self.session_catalog is None:
            raise RuntimeInventoryError("runtime inventory missing required service: session_catalog")
        return self.session_catalog

    def require_session_lifecycle(self) -> SessionLifecycle:
        if self.session_lifecycle is None:
            raise RuntimeInventoryError("runtime inventory missing required service: session_lifecycle")
        return self.session_lifecycle

    def require_run_lifecycle(self) -> RunLifecycle:
        if self.run_lifecycle is None:
            raise RuntimeInventoryError("runtime inventory missing required service: run_lifecycle")
        return self.run_lifecycle

    def require_config_policy(self) -> ConfigPolicyService:
        if self.config_policy is None:
            raise RuntimeInventoryError("runtime inventory missing required service: config_policy")
        return self.config_policy

    def require_workspace_volume_gateway(self) -> WorkspaceVolumeGateway:
        if self.workspace_volume_gateway is None:
            raise RuntimeInventoryError("runtime inventory missing required service: workspace_volume_gateway")
        return self.workspace_volume_gateway

    def require_workspace_file_service(self) -> WorkspaceFileService:
        if self.workspace_file_service is None:
            raise RuntimeInventoryError("runtime inventory missing required service: workspace_file_service")
        return self.workspace_file_service

    @property
    def db_engine(self) -> AsyncEngine | None:
        return self.database.engine

    @property
    def session_manager(self) -> RuntimeSessionManager | None:
        if self.run_environment_resources is None:
            return None
        return self.run_environment_resources.session_manager


def get_runtime_inventory(app: FastAPI) -> RuntimeInventory | None:
    """Return the currently attached runtime inventory, if any."""
    inventory = getattr(app.state, "runtime_inventory", None)
    if isinstance(inventory, RuntimeInventory):
        return inventory
    return None


def install_runtime_inventory(app: FastAPI, inventory: RuntimeInventory) -> RuntimeInventory:
    """Publish a complete runtime graph and mark composition ready last."""
    inventory.validate_complete()
    app.state.runtime_inventory = inventory
    app.state.composition_ready = True
    return inventory


def clear_runtime_inventory(app: FastAPI) -> RuntimeInventory | None:
    """Detach runtime services before the owning lifespan disposes resources."""
    app.state.composition_ready = False
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
    close_preparation = getattr(preparation, "aclose", None)
    if callable(close_preparation):
        try:
            result = await close_preparation()
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
    "RuntimeInventoryError",
    "RuntimeProcessResources",
    "RuntimeSessionManager",
    "SettlingRunStateStore",
    "clear_runtime_inventory",
    "close_inventory_services",
    "get_runtime_inventory",
    "install_runtime_inventory",
    "no_provider_recovery_fence",
]
