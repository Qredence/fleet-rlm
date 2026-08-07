"""Typed runtime inventory publication for FastAPI lifespan composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
from fleet_rlm.chat.turn_coordinator import TurnCoordinator
from fleet_rlm.chat.turn_lifecycle import TurnLifecycle
from fleet_rlm.chat.turn_preparation import TurnPreparation
from fleet_rlm.config_policy import ConfigPolicyService
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.volume_storage import VolumeTreeFs, WorkspaceVolumeGateway
from fleet_rlm.files.workspace_access import WorkspaceFileService
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.catalog import SessionCatalog


class SettlingTurnStore(Protocol):
    """Turn store surface needed by startup recovery."""

    async def reconcile_settling(
        self,
        fence: Callable[[UUID], Awaitable[None]] | None = None,
        *,
        deadline: float | None = None,
    ) -> ReconciliationSummary: ...


class RuntimeSessionManager(Protocol):
    """Provider session manager surface needed by startup recovery."""

    async def fence_session(self, session_id: UUID) -> None: ...


class RuntimeProcessResources(Protocol):
    """Closeable process-scoped resources owned by one runtime composition."""

    @property
    def session_manager(self) -> RuntimeSessionManager: ...

    async def adispose(self) -> None: ...


class AsyncCloseable(Protocol):
    async def close(self) -> None: ...


class RuntimeInventoryError(RuntimeError):
    """Raised when a runtime inventory is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseLifecycle:
    """Database handles created for one application lifespan."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    dispose_engine: bool = True

    async def aclose(self) -> None:
        if self.dispose_engine and self.engine is not None:
            await self.engine.dispose()


@dataclass(frozen=True, slots=True)
class RuntimeInventory:
    """Complete dynamic service graph installed for one application lifespan."""

    turn_coordinator: TurnCoordinator | None = None
    attachment_lifecycle: AttachmentLifecycle | None = None
    artifact_reader: ArtifactReader | None = None
    session_catalog: SessionCatalog | None = None
    turn_lifecycle: TurnLifecycle | None = None
    turn_preparation: TurnPreparation | None = None
    turn_cleanup_supervisor: TurnCleanupSupervisor | None = None
    turn_state_store: SettlingTurnStore | None = None
    config_policy: ConfigPolicyService | None = None
    database: RuntimeDatabaseLifecycle = field(default_factory=RuntimeDatabaseLifecycle)
    run_environment_resources: RuntimeProcessResources | None = None
    model_bundle: RLMModelBundle | None = None
    workspace_volume_gateway: WorkspaceVolumeGateway | None = None
    workspace_file_service: WorkspaceFileService | None = None
    workspace_volume_mirror: VolumeTreeFs | None = None

    _REQUIRED_ROUTE_FIELDS: ClassVar[tuple[str, ...]] = (
        "turn_coordinator",
        "attachment_lifecycle",
        "artifact_reader",
        "session_catalog",
        "turn_lifecycle",
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
    def session_manager(self) -> RuntimeSessionManager | None:
        if self.run_environment_resources is None:
            return None
        return self.run_environment_resources.session_manager

    @property
    def rlm_model_bundle(self) -> RLMModelBundle | None:
        return self.model_bundle


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


__all__ = [
    "AsyncCloseable",
    "RuntimeDatabaseLifecycle",
    "RuntimeInventory",
    "RuntimeInventoryError",
    "RuntimeProcessResources",
    "RuntimeSessionManager",
    "SettlingTurnStore",
    "clear_runtime_inventory",
    "get_runtime_inventory",
    "install_runtime_inventory",
]
