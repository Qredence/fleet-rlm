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
from typing import TYPE_CHECKING, ClassVar, Protocol
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from fleet_rlm.daytona.broker import SyncBridgeDispatcher

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments.lifecycle import AttachmentLifecycle
from fleet_rlm.chat.preparation import RunPreparation
from fleet_rlm.chat.run_lifecycle import RunLifecycle
from fleet_rlm.chat.turn_runtime import TurnRuntime
from fleet_rlm.config_policy import ConfigPolicyService
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry
from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
from fleet_rlm.sessions.catalog import SessionCatalog
from fleet_rlm.workspace.storage import VolumeTreeFs, WorkspaceVolumeGateway
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


class RuntimeProcessResources(Protocol):
    """Closeable process-scoped resources owned by one runtime composition."""

    @property
    def session_manager(self) -> RuntimeSessionManager: ...

    async def adispose(self, *, drain_seconds: float = 30.0) -> bool | None: ...


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

    # Compatibility field name retained for existing route/dependency users;
    # TurnRuntime is the implementation owner.
    turn_coordinator: TurnRuntime | None = None
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
    session_runtime_registry: SessionRLMRegistry | None = None
    workspace_volume_gateway: WorkspaceVolumeGateway | None = None
    workspace_file_service: WorkspaceFileService | None = None
    workspace_volume_mirror: VolumeTreeFs | None = None
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

    _REQUIRED_ROUTE_FIELDS: ClassVar[tuple[str, ...]] = (
        "turn_coordinator",
        "attachment_lifecycle",
        "artifact_reader",
        "session_catalog",
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
    def turn_runtime(self) -> TurnRuntime | None:
        """Return the canonical TurnRuntime through the transition alias."""
        return self.turn_coordinator

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


__all__ = [
    "RuntimeDatabaseLifecycle",
    "RuntimeInventory",
    "RuntimeInventoryError",
    "RuntimeProcessResources",
    "RuntimeSessionManager",
    "SettlingRunStateStore",
    "clear_runtime_inventory",
    "get_runtime_inventory",
    "install_runtime_inventory",
]
