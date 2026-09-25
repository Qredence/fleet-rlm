"""Construct and close Daytona services within one FastAPI lifespan."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import FastAPI

from fleet_rlm.app_services import (
    DaytonaRuntimeOwner,
    DaytonaRuntimeSurface,
    RouteServices,
    RuntimeDatabaseLifecycle,
    RuntimeInventory,
    SettlingRunStateStore,
)
from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.config.settings import Settings
from fleet_rlm.config.validation import CompositionError, require_daytona_settings
from fleet_rlm.daytona.interpreter import SyncBridgeDispatcher, sync_sandbox, tombstone_sync_sandbox
from fleet_rlm.persistence.database import ensure_database_compatible
from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.budget import BudgetLimits
from fleet_rlm.rlm.program import RLMModelBundle, rlm_options
from fleet_rlm.rlm.recursion import recursive_rlm_options
from fleet_rlm.sessions.lifecycle import SessionActiveTurnDrain, SessionLifecycle
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.turn_preparation import DaytonaCapabilityPreparer, TurnPreparationPlan
from fleet_rlm.workspace.memory import MemoryOutboxReconciler, run_deferred_memory_outbox_reconcile
from fleet_rlm.workspace.mounted_gateway import (
    DaytonaWorkspaceGateway,
    DaytonaWorkspaceVolumeGateway,
    run_deferred_orphan_cleanup,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fleet_rlm.daytona.runtime import DaytonaRuntime, DaytonaSandboxSpec
    from fleet_rlm.paths import VolumePaths

_STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS = 15
_STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS = 75.0
_COMPOSITION_DISPOSAL_RETRY_BUDGET_SECONDS = 60.0
_COMPOSITION_DISPOSAL_TASKS: set[asyncio.Task[Any]] = set()
_COMPOSITION_DISPOSAL_OWNERS: dict[int, RuntimeInventory] = {}


async def _dispose_components(
    *,
    resources: DaytonaRuntimeOwner | None,
    gateway: object | None,
    database: RuntimeDatabaseLifecycle | None = None,
    suppress_errors: bool,
) -> bool:
    """Dispose available runtime components, optionally suppressing errors."""
    first_error: Exception | None = None
    settled = True
    resources_settled = True
    for target, method_name in ((resources, "adispose"), (gateway, "close"), (database, "aclose")):
        # The gateway shares the Daytona client with the runtime owner.
        # If provider ownership is still pending, do not invoke a second
        # client-bound close hook; independent database cleanup still runs.
        if target is gateway and not resources_settled:
            continue
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = await method()
            if target is resources and result is False:
                resources_settled = False
                settled = False
            elif result is False:
                settled = False
        except Exception as exc:
            if target is resources:
                resources_settled = False
            settled = False
            if first_error is None:
                first_error = exc
    if first_error is not None and not suppress_errors:
        raise first_error
    return settled


async def _cancel_orphan_cleanup(task: asyncio.Task[None] | None) -> None:
    """Cancel and settle the owned orphan sweep before disposing its resources."""
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning("Daytona orphan cleanup failed while settling shutdown", exc_info=True)


async def _finish_daytona_disposal(
    inventory: RuntimeInventory,
    dispatcher: SyncBridgeDispatcher,
    composition_loop: asyncio.AbstractEventLoop | None,
) -> bool:
    """Retry deferred composition teardown before relinquishing bridge authority."""
    retry_deadline = asyncio.get_running_loop().time() + _COMPOSITION_DISPOSAL_RETRY_BUDGET_SECONDS
    while asyncio.get_running_loop().time() < retry_deadline:
        runner = getattr(inventory, "runner", None)
        close_runner = getattr(runner, "aclose", None)
        if callable(close_runner):
            with contextlib.suppress(BaseException):
                await close_runner(drain_seconds=1)

        preparation = getattr(inventory, "run_preparation", None)
        from fleet_rlm.app_services import close_preparation_services

        with contextlib.suppress(BaseException):
            await close_preparation_services(preparation)

        resources = getattr(inventory, "daytona_runtime_owner", None)
        components_settled = False
        with contextlib.suppress(BaseException):
            components_settled = await _dispose_components(
                resources=resources,
                gateway=inventory.route_services.workspace_volume_gateway,
                database=getattr(inventory, "database", None),
                suppress_errors=True,
            )
        cleanup = getattr(inventory, "run_cleanup_supervisor", None)
        cleanup_pending = bool(getattr(cleanup, "active_jobs", 0)) if cleanup is not None else False
        pending = (
            not components_settled or cleanup_pending or bool(resources is not None and resources.has_pending_cleanup())
        )
        if not pending:
            if composition_loop is not None:
                dispatcher.clear_loop(composition_loop)
            return True

        # Wait briefly for resources owned by this composition. Foreign-loop
        # work remains unresolved, so this task never clears a bridge needed elsewhere.
        if resources is not None:
            with contextlib.suppress(BaseException):
                await resources.wait_pending_cleanup(timeout=0.25)
        await asyncio.sleep(0.25)
    logger.warning("deferred Daytona composition disposal budget expired; provider ownership remains fenced")
    return False


def _retain_composition_disposal(
    task: asyncio.Task[Any],
    *,
    inventory: RuntimeInventory,
) -> None:
    """Retain unresolved ownership without moving loop-bound SDK resources."""
    _COMPOSITION_DISPOSAL_OWNERS[id(inventory)] = inventory
    _COMPOSITION_DISPOSAL_TASKS.add(task)

    def settled(completed: asyncio.Task[Any]) -> None:
        _COMPOSITION_DISPOSAL_TASKS.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.warning(
                "deferred Daytona composition disposal failed",
                extra={"error_type": type(error).__name__},
            )
        elif completed.result():
            _COMPOSITION_DISPOSAL_OWNERS.pop(id(inventory), None)

    task.add_done_callback(settled)


async def _reconcile_daytona_settling(
    run_state: SettlingRunStateStore,
    runtime: DaytonaRuntimeSurface,
    *,
    fence_timeout: float = _STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> ReconciliationSummary:
    """Reconcile stale settling turns using bounded runtime fencing."""

    async def bounded_fence(session_id: UUID) -> None:
        remaining = fence_timeout
        if deadline is not None:
            remaining = min(remaining, deadline - asyncio.get_running_loop().time())
        if remaining <= 0:
            raise TimeoutError("startup recovery budget exhausted")
        fence_deadline = asyncio.get_running_loop().time() + remaining
        fence = runtime.fence_session
        try:
            accepts_deadline = "deadline" in inspect.signature(fence).parameters
        except (TypeError, ValueError):
            accepts_deadline = False
        await asyncio.wait_for(
            fence(session_id, deadline=fence_deadline) if accepts_deadline else fence(session_id),
            timeout=remaining,
        )

    return await run_state.reconcile_settling(bounded_fence, deadline=deadline)


async def build_daytona_composition(
    settings: Settings,
    *,
    skill_catalog: SkillCatalog,
    dispatcher: SyncBridgeDispatcher,
) -> RuntimeInventory:
    """
    Construct the Daytona runtime inventory and recover cleanly from initialization failures.

    Parameters:
        settings (Settings): Configuration used to create and validate the runtime.
        skill_catalog (SkillCatalog): Catalog of skills available to the runtime.
        dispatcher (SyncBridgeDispatcher): Composition-owned bridge for synchronous Daytona operations.

    Returns:
        RuntimeInventory: The initialized Daytona runtime services and background tasks.
    """
    from fleet_rlm.rlm.program import assert_dspy_version

    assert_dspy_version()
    require_daytona_settings(settings)

    from fleet_rlm.api.local_scope import LocalScope
    from fleet_rlm.attachments import (
        AttachmentLifecycleService,
        DaytonaRunAttachmentPathPolicy,
    )
    from fleet_rlm.config.policy import ConfigPolicyService
    from fleet_rlm.daytona.errors import map_provider_error
    from fleet_rlm.daytona.runtime import DEFAULT_IDLE_STOP_SECONDS, DaytonaRuntime, sandbox_spec_from_settings
    from fleet_rlm.paths import volume_paths_from_settings
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
    from fleet_rlm.persistence.repositories import (
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
        SqlAlchemyRunStateStore,
        SqlAlchemySandboxBindingStore,
        SqlAlchemySessionCatalog,
    )
    from fleet_rlm.rlm.execution import RLMRunner
    from fleet_rlm.rlm.ownership import RunCleanupSupervisor
    from fleet_rlm.rlm.program import build_model_bundle
    from fleet_rlm.sessions.task import SessionTaskService
    from fleet_rlm.turn_settlement import RunSettlementPlan, bind_settlement
    from fleet_rlm.turns import TurnRuntime
    from fleet_rlm.workspace.mounted_gateway import (
        DaytonaWorkspaceGateway,
        DaytonaWorkspaceVolumeGateway,
    )
    from fleet_rlm.workspace.workspace import WorkspaceAccessGateway, WorkspaceFileService

    resolved = settings
    require_daytona_settings(resolved)
    sandbox_spec = sandbox_spec_from_settings(resolved)
    volume_paths = volume_paths_from_settings(resolved)
    engine = create_async_engine_from_url(resolved.database_url or "")
    database_lifecycle: RuntimeDatabaseLifecycle | None = None
    runtime: DaytonaRuntime | None = None
    gateway: object | None = None
    orphan_cleanup_task: asyncio.Task[None] | None = None
    memory_outbox_task: asyncio.Task[None] | None = None
    try:
        # Fail closed on an unreachable or non-head database, inside the
        # cleanup scope so the engine above is always disposed on failure.
        await ensure_database_compatible(
            resolved.database_url or "",
            repo_root=Path(__file__).resolve().parents[2],
        )
        session_factory = create_session_factory(engine)
        database_lifecycle = RuntimeDatabaseLifecycle(engine=engine, session_factory=session_factory)
        cleanup = RunCleanupSupervisor(max_jobs=8)
        bindings = SqlAlchemySandboxBindingStore(session_factory)
        model_bundle = build_model_bundle(resolved)
        runtime = DaytonaRuntime.from_settings(
            resolved,
            bindings=bindings,
            cleanup=cleanup,
            sandbox_spec=sandbox_spec,
            max_active_leases=resolved.max_active_daytona_leases,
            idle_stop_seconds=DEFAULT_IDLE_STOP_SECONDS,
            execution_output_cap=resolved.rlm_max_execution_output_chars,
            execution_timeout_s=resolved.rlm_execution_timeout_s,
            dispatcher=dispatcher,
        )
        mounted_workspace_gateway = DaytonaWorkspaceGateway(
            runtime=runtime,
            paths=volume_paths,
            max_file_bytes=resolved.max_upload_bytes,
            map_error=map_provider_error,
        )
        gateway = DaytonaWorkspaceVolumeGateway(
            mounted_workspace_gateway,
            mount_path=resolved.volume_mount_path,
        )
        attachment_lifecycle = AttachmentLifecycleService(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=gateway,
            paths=DaytonaRunAttachmentPathPolicy(volume_paths),
            max_bytes=resolved.max_upload_bytes,
        )
        artifact_catalog = SqlAlchemyArtifactCatalog(session_factory)
        artifact_reader = ArtifactReader(
            catalog=artifact_catalog,
            blobs=gateway,
        )
        workspace_file_service = WorkspaceFileService(cast(WorkspaceAccessGateway, mounted_workspace_gateway))
        local_scope = LocalScope()
        session_catalog = SqlAlchemySessionCatalog(session_factory)
        task_service = SessionTaskService(session_catalog, gateway, volume_paths)
        startup_started = asyncio.get_running_loop().time()
        startup_deadline = startup_started + _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS
        run_preparation = build_run_preparation(
            runtime,
            attachment_lifecycle=attachment_lifecycle,
            skill_catalog=skill_catalog,
            settings=resolved,
            models=model_bundle,
            volume_paths=volume_paths,
            sandbox_spec=sandbox_spec,
            dispatcher=dispatcher,
            artifact_reader=artifact_reader,
            workspace_gateway=mounted_workspace_gateway,
            volume_gateway=gateway,
            task_service=task_service,
        )
        run_state = SqlAlchemyRunStateStore(
            session_factory,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        memory_outbox = SqlAlchemyMemoryPromotionOutbox(session_factory)
        settlement = RunSettlementPlan(
            run_state,
            max_artifact_bytes=resolved.max_artifact_bytes,
            heartbeat_seconds=resolved.run_heartbeat_seconds,
            stale_after_seconds=resolved.run_stale_after_seconds,
            cleanup=cleanup,
            memory_outbox=memory_outbox,
        )
        lifecycle = bind_settlement(settlement)
        recovery = await _reconcile_daytona_settling(
            run_state,
            runtime,
            deadline=startup_deadline,
        )
        recovery_elapsed_ms = int((asyncio.get_running_loop().time() - startup_started) * 1000)
        logger.info(
            "Daytona startup recovery complete phase=settling_recovery candidates=%d recovered=%d "
            "fence_failures=%d skipped=%d budget_exhausted=%s elapsed_ms=%d",
            recovery.candidates,
            recovery.recovered,
            recovery.fence_failures,
            recovery.skipped,
            recovery.budget_exhausted,
            recovery_elapsed_ms,
        )
        if recovery.fence_failures or recovery.skipped:
            logger.warning(
                "Daytona startup recovery left retryable work phase=settling_recovery fence_failures=%d "
                "skipped=%d budget_exhausted=%s",
                recovery.fence_failures,
                recovery.skipped,
                recovery.budget_exhausted,
            )

        # P23/QRE-166: DB-only startup step — reclaim stale delivery claims and
        # log bounded outbox state inside the shared startup budget. Delivery
        # itself is deferred to the tracked sweep task below (ephemeral
        # sandbox cold starts would blow the startup budget).
        outbox_reclaimed = await memory_outbox.reclaim_stale(now=datetime.now(UTC))
        outbox_summary = await memory_outbox.summary()
        logger.info(
            "Memory promotion outbox startup phase=reclaim reclaimed=%d pending=%d completing=%d "
            "completed=%d failed=%d",
            outbox_reclaimed,
            outbox_summary.pending,
            outbox_summary.completing,
            outbox_summary.completed,
            outbox_summary.failed,
        )

        # Defer the non-critical orphan sweep until after readiness; it creates
        # ephemeral Daytona sandboxes whose cold provisioning routinely exceeds
        # the startup budget (see supervisor._READY_TIMEOUT_SECONDS).
        orphan_cleanup_task = asyncio.get_running_loop().create_task(
            run_deferred_orphan_cleanup(
                gateway,
                workspace_id=local_scope.workspace_id,
                paths=volume_paths,
                artifact_catalog=artifact_catalog,
            ),
            name="fleet-daytona-orphan-cleanup",
        )

        @contextlib.asynccontextmanager
        async def open_memory(workspace_id: UUID):
            """Open provider-neutral Memory over one bounded Workspace Agent root."""
            from fleet_rlm.workspace.memory import build_workspace_memory_store
            from fleet_rlm.workspace.storage import DaytonaSandboxWorkspaceStorage, WorkspaceMemoryStorage

            memory_view: Any | None = None
            async with mounted_workspace_gateway.open_sandbox(
                workspace_id,
                purpose="memory-outbox-reconcile",
            ) as sandbox:
                try:
                    memory_view = sync_sandbox(
                        sandbox,
                        asyncio.get_running_loop(),
                        dispatcher,
                    )
                    memory_session = DaytonaSandboxWorkspaceStorage(
                        memory_view,
                        volume_root=str(volume_paths.mount_path),
                        root=str(volume_paths.mount_path),
                        max_file_bytes=resolved.max_upload_bytes,
                        allow_volume_root=True,
                    )
                    yield build_workspace_memory_store(
                        WorkspaceMemoryStorage(memory_session),
                        max_upload_bytes=resolved.max_upload_bytes,
                    )
                finally:
                    if memory_view is not None:
                        tombstone_sync_sandbox(memory_view)

        memory_outbox_reconciler = MemoryOutboxReconciler(
            memory_outbox,
            open_memory=open_memory,
            allowed_categories=lambda: tuple(resolved.rlm_autonomous_memory_categories),
        )
        memory_outbox_task = asyncio.get_running_loop().create_task(
            run_deferred_memory_outbox_reconcile(memory_outbox_reconciler),
            name="fleet-memory-outbox-reconcile",
        )

        runner = RLMRunner(verbose=resolved.rlm_verbose)
        coordinator = TurnRuntime(
            lifecycle=lifecycle,
            preparation=run_preparation,
            runner=runner,
            turn_timeout_seconds=resolved.turn_timeout_seconds,
            cleanup=cleanup,
            claim_loss_fence=runtime.fence_session,
            mlflow_tracing_enabled=resolved.mlflow_tracing_enabled,
            mlflow_expose_trace_id=resolved.mlflow_expose_trace_id,
        )
        session_lifecycle = SessionLifecycle(
            session_catalog,
            runtime,
            active_turn_drain=cast(SessionActiveTurnDrain, run_preparation.environments),
        )
        route_services = RouteServices(
            turn_runtime=coordinator,
            attachment_lifecycle=attachment_lifecycle,
            artifact_reader=artifact_reader,
            session_catalog=session_catalog,
            session_lifecycle=session_lifecycle,
            config_policy=ConfigPolicyService.from_settings(resolved),
            workspace_volume_gateway=gateway,
            workspace_file_service=workspace_file_service,
            daytona_runtime=runtime,
            session_task_service=task_service,
        )
        return RuntimeInventory(
            route_services=route_services,
            daytona_runtime_owner=runtime,
            bridge_dispatcher=dispatcher,
            runner=runner,
            run_cleanup_supervisor=cleanup,
            run_preparation=run_preparation,
            run_state_store=run_state,
            database=database_lifecycle,
            model_bundle=model_bundle,
            orphan_cleanup_task=orphan_cleanup_task,
            memory_outbox_task=memory_outbox_task,
        )
    except BaseException:
        await _cancel_orphan_cleanup(orphan_cleanup_task)
        await _cancel_orphan_cleanup(memory_outbox_task)
        if runtime is None and database_lifecycle is None:
            await engine.dispose()
        else:
            await _dispose_components(
                resources=runtime,
                gateway=gateway,
                database=database_lifecycle,
                suppress_errors=True,
            )
        raise


@asynccontextmanager
async def daytona_services(app: FastAPI, settings: Settings) -> AsyncIterator[RuntimeInventory]:
    """Construct and own one Daytona service graph for a FastAPI lifespan."""
    skill_catalog = getattr(app.state, "skill_catalog", None)
    if not isinstance(skill_catalog, SkillCatalog):
        raise CompositionError("bundled Skill catalog is unavailable")
    dispatcher = SyncBridgeDispatcher()
    owning_loop = asyncio.get_running_loop()
    dispatcher.set_loop(owning_loop)
    try:
        inventory = await build_daytona_composition(settings, skill_catalog=skill_catalog, dispatcher=dispatcher)
    except BaseException:
        dispatcher.clear_loop(owning_loop)
        raise
    try:
        yield inventory
    except BaseException:
        try:
            await close_daytona_services(inventory)
        except BaseException:
            logger.warning("Daytona service cleanup failed after startup or lifespan failure", exc_info=True)
        raise
    else:
        await close_daytona_services(inventory)


async def close_daytona_services(inventory: RuntimeInventory) -> None:
    """Drain owned work before releasing provider, gateway, and database resources."""
    from fleet_rlm.app_services import close_inventory_services

    errors: list[BaseException] = []

    async def phase(awaitable: Any) -> Any:
        try:
            return await awaitable
        except BaseException as exc:
            errors.append(exc)
            return None

    # Stop accepting detached work first, but never let one cleanup hook skip
    # runtime fencing or provider retirement.
    await phase(_cancel_orphan_cleanup(getattr(inventory, "orphan_cleanup_task", None)))
    await phase(_cancel_orphan_cleanup(getattr(inventory, "memory_outbox_task", None)))
    service_close = await close_inventory_services(inventory, drain_seconds=30)
    errors.extend(service_close.errors)
    if service_close.cancellation is not None:
        errors.append(service_close.cancellation)
    cleanup = getattr(inventory, "run_cleanup_supervisor", None)

    deferred_settled = (
        not service_close.errors and service_close.cancellation is None and service_close.preparation_settled
    )

    cleanup_pending = bool(getattr(cleanup, "active_jobs", 0)) if cleanup is not None else False
    ownership_pending = not deferred_settled or cleanup_pending
    if ownership_pending:
        logger.warning(
            "Daytona runtime disposal retained resources for owned cleanup",
            extra={"deferred_runtime": not deferred_settled, "cleanup_jobs": int(cleanup_pending)},
        )
    else:
        try:
            components_settled = await _dispose_components(
                resources=getattr(inventory, "daytona_runtime_owner", None),
                gateway=inventory.route_services.workspace_volume_gateway,
                database=getattr(inventory, "database", None),
                suppress_errors=False,
            )
            if components_settled is False:
                ownership_pending = True
        except BaseException as exc:
            errors.append(exc)

    # A pending owner may still need this bridge. Keep a composition-owned
    # retry task alive and clear the dispatcher only after every provider and
    # cleanup owner has settled; otherwise clear it even when a close component
    # failed, preventing stale loop registration.
    dispatcher = getattr(inventory, "bridge_dispatcher", None)
    if ownership_pending and isinstance(dispatcher, SyncBridgeDispatcher):
        composition_loop = dispatcher.service_loop()
        if composition_loop is asyncio.get_running_loop():
            deferred = asyncio.create_task(
                _finish_daytona_disposal(inventory, dispatcher, composition_loop),
                name="fleet-daytona-composition-disposal",
            )
            _retain_composition_disposal(
                deferred,
                inventory=inventory,
            )
        else:
            # Retain the inventory for recovery; loop-bound clients and pending
            # requests cannot safely be transferred to a new event loop.
            _COMPOSITION_DISPOSAL_OWNERS[id(inventory)] = inventory
            logger.warning("Daytona disposal requires its owning application loop; ownership remains retained")
    elif isinstance(dispatcher, SyncBridgeDispatcher):
        dispatcher.clear_loop(dispatcher.service_loop())

    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("Daytona composition disposal failed", errors)


def build_run_preparation(
    runtime: DaytonaRuntime,
    *,
    attachment_lifecycle: Any,
    skill_catalog: SkillCatalog,
    settings: Settings,
    models: RLMModelBundle,
    volume_paths: VolumePaths,
    sandbox_spec: DaytonaSandboxSpec,
    dispatcher: SyncBridgeDispatcher,
    workspace_gateway: DaytonaWorkspaceGateway,
    volume_gateway: DaytonaWorkspaceVolumeGateway,
    artifact_reader: ArtifactReader | None = None,
    task_service: Any | None = None,
) -> TurnPreparationPlan:
    """
    Create a Daytona run preparer configured with models, runtime limits,
    attachments, environments, and live capabilities.

    Parameters:
        runtime (DaytonaRuntime): Process-owned Daytona runtime for Session and child resources.
        attachment_lifecycle (Any): Attachment lifecycle used during run preparation.
        skill_catalog (SkillCatalog): Skills available to live capabilities.
        settings (Settings): Runtime and budget configuration.
        models (RLMModelBundle): Models used for run execution.

    Returns:
        TurnPreparationPlan: Immutable Turn preparation inputs.
    """
    from fleet_rlm.daytona.turn_environment import _DaytonaEnvironmentProvider

    return TurnPreparationPlan(
        models=models,
        options=rlm_options(settings),
        recursive_options=recursive_rlm_options(settings),
        wrap_up_seconds=settings.rlm_wrap_up_seconds,
        budget_limits=BudgetLimits(
            provider_attempts=settings.rlm_max_provider_attempts,
            tool_calls=settings.rlm_max_tool_calls,
            recursive_children=(settings.rlm_recursion_max_calls if settings.rlm_recursion_enabled else 0),
            execution_output_bytes=settings.rlm_max_execution_output_bytes,
            finalization_attempts=settings.rlm_finalization_attempts,
            finalization_seconds=settings.rlm_wrap_up_seconds,
        ),
        attachments=attachment_lifecycle,
        environments=_DaytonaEnvironmentProvider(
            runtime=runtime,
            settings=settings,
            volume_paths=volume_paths,
            sandbox_spec=sandbox_spec,
            dispatcher=dispatcher,
            workspace_gateway=workspace_gateway,
            volume_gateway=volume_gateway,
        ),
        task_service=task_service,
        capabilities=DaytonaCapabilityPreparer(
            settings,
            skill_catalog,
            volume_paths=volume_paths,
            artifact_reader=artifact_reader,
            task_service=task_service,
        ),
    )
