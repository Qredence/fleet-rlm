"""Daytona runtime composition and process-lifetime resource ownership."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread, current_thread
from typing import Any, cast
from uuid import UUID

from fastapi import FastAPI

from fleet_rlm.composition.common import CompositionError
from fleet_rlm.composition.daytona_environment import DaytonaRuntimeResources
from fleet_rlm.composition.inventory import (
    RuntimeDatabaseLifecycle,
    RuntimeInventory,
    RuntimeProcessResources,
    RuntimeSessionManager,
    SettlingRunStateStore,
    clear_runtime_inventory,
    install_runtime_inventory,
)
from fleet_rlm.config import Settings
from fleet_rlm.daytona.broker import SyncBridgeDispatcher, sync_sandbox, tombstone_sync_sandbox
from fleet_rlm.daytona.session_manager import DEFAULT_IDLE_STOP_SECONDS
from fleet_rlm.persistence.database import ensure_database_compatible
from fleet_rlm.persistence.repositories.outbox import SqlAlchemyMemoryPromotionOutbox
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.workspace.memory import MemoryOutboxReconciler

logger = logging.getLogger(__name__)
_ORPHAN_CLEANUP_TIMEOUT_SECONDS = 60
_STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS = 15
_STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS = 75.0
_COMPOSITION_DISPOSAL_RETRY_BUDGET_SECONDS = 60.0
_COMPOSITION_DISPOSAL_TASKS: set[asyncio.Task[Any]] = set()
_COMPOSITION_DISPOSAL_MONITORS: set[Thread] = set()


def require_daytona_settings(settings: Settings) -> None:
    """Validate that all required Daytona runtime settings are configured."""
    if settings.run_environment != "daytona":
        raise CompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
    if not (settings.daytona_snapshot or "").strip():
        missing.append("FLEET_DAYTONA_SNAPSHOT")
    from fleet_rlm.rlm.program import has_llm_credentials, sanitize_base_url

    if not has_llm_credentials(settings):
        missing.append("configured provider API key")
    if any(
        role.api_key_env == "DATABRICKS_TOKEN" and not sanitize_base_url(role.base_url)
        for role in (settings.root_lm, settings.sub_lm)
    ):
        missing.append("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL")
    if not (settings.database_url or "").strip():
        missing.append("FLEET_DATABASE_URL")
    if missing:
        raise CompositionError("Daytona composition missing required settings: " + ", ".join(missing))


async def _dispose_components(
    *,
    resources: RuntimeProcessResources | None,
    gateway: object | None,
    database: RuntimeDatabaseLifecycle | None = None,
    suppress_errors: bool,
) -> bool:
    """Dispose available runtime components, optionally suppressing errors."""
    first_error: Exception | None = None
    settled = True
    resources_settled = True
    for target, method_name in ((resources, "adispose"), (gateway, "close"), (database, "aclose")):
        # The gateway shares the Daytona client with RuntimeProcessResources.
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
) -> None:
    """Retry deferred composition teardown before relinquishing bridge authority."""
    from fleet_rlm.composition.daytona_environment import (
        has_pending_resource_cleanup,
        wait_resource_cleanup,
    )
    from fleet_rlm.daytona.sandbox_lease import has_pending_lease_ownership, wait_lease_ownership

    retry_deadline = asyncio.get_running_loop().time() + _COMPOSITION_DISPOSAL_RETRY_BUDGET_SECONDS
    while asyncio.get_running_loop().time() < retry_deadline:
        runner = getattr(inventory, "runner", None)
        close_runner = getattr(runner, "aclose", None)
        if callable(close_runner):
            with contextlib.suppress(BaseException):
                await close_runner(drain_seconds=1)

        registry = getattr(inventory, "session_runtime_registry", None)
        if registry is not None:
            with contextlib.suppress(BaseException):
                await registry.shutdown(drain_seconds=1)
            wait_deferred = getattr(registry, "wait_deferred_closes", None)
            if callable(wait_deferred):
                with contextlib.suppress(BaseException):
                    await wait_deferred(timeout=1)

        preparation = getattr(inventory, "run_preparation", None)
        close_preparation = getattr(preparation, "aclose", None)
        if callable(close_preparation):
            with contextlib.suppress(BaseException):
                await close_preparation()

        resources = getattr(inventory, "run_environment_resources", None)
        components_settled = False
        with contextlib.suppress(BaseException):
            components_settled = await _dispose_components(
                resources=resources,
                gateway=getattr(inventory, "workspace_volume_gateway", None),
                database=getattr(inventory, "database", None),
                suppress_errors=True,
            )
        cleanup = getattr(inventory, "run_cleanup_supervisor", None)
        cleanup_pending = bool(getattr(cleanup, "active_jobs", 0)) if cleanup is not None else False
        pending = (
            not components_settled
            or cleanup_pending
            or bool(getattr(preparation, "has_pending_acquisitions", False))
            or bool(getattr(registry, "has_deferred_closes", False))
            or has_pending_resource_cleanup()
            or has_pending_lease_ownership()
        )
        if not pending:
            if composition_loop is not None:
                dispatcher.clear_loop(composition_loop)
            return

        # Wait briefly for owned tasks that are still attached to this loop.
        # Foreign-loop ownership is deliberately reported as unresolved by the
        # wait helpers, so this task never clears a bridge needed elsewhere.
        with contextlib.suppress(BaseException):
            await wait_resource_cleanup(timeout=0.25)
        with contextlib.suppress(BaseException):
            await wait_lease_ownership(timeout=0.25)
        await asyncio.sleep(0.25)
    logger.warning("deferred Daytona composition disposal budget expired; provider ownership remains fenced")


def _retain_composition_disposal(
    task: asyncio.Task[Any],
    *,
    inventory: RuntimeInventory,
    dispatcher: SyncBridgeDispatcher,
    composition_loop: asyncio.AbstractEventLoop,
) -> None:
    """Retain deferred teardown, including when its loop is destroyed.

    FastAPI normally runs lifespan finalizers on a live loop.  If that loop is
    stopped while a provider request is still owned, however, asyncio cancels
    the deferred task and destroys the loop before a later retry can run.  A
    tiny daemon monitor keeps the inventory/dispatcher fenced and retries on
    the original loop when possible, then on a disposable loop after the
    original loop has stopped.  It never closes the client while ownership is
    unresolved; ``_finish_daytona_disposal`` performs that final check.
    """
    _COMPOSITION_DISPOSAL_TASKS.add(task)
    woke = Event()

    def settled(completed: asyncio.Task[Any]) -> None:
        _COMPOSITION_DISPOSAL_TASKS.discard(completed)
        woke.set()
        if completed.cancelled():
            return
        with contextlib.suppress(BaseException):
            error = completed.exception()
        if error is not None:
            logger.warning(
                "deferred Daytona composition disposal failed",
                extra={"error_type": type(error).__name__},
            )

    task.add_done_callback(settled)

    def retry() -> None:
        """Retry cancellation/loop-stop teardown without blocking the loop."""
        try:
            while not task.done():
                if composition_loop.is_closed() or not composition_loop.is_running():
                    break
                woke.wait(0.1)
                woke.clear()

            # A normally completed retry has already made the ownership
            # decision.  Only cancellation or loop shutdown needs another
            # owner; a completed-but-unsuccessful pass leaves global provider
            # fences in place for the next composition/process owner.
            if task.done() and not task.cancelled():
                return

            if composition_loop.is_running() and not composition_loop.is_closed():
                woke.clear()
                try:
                    retry_future = asyncio.run_coroutine_threadsafe(
                        _finish_daytona_disposal(inventory, dispatcher, composition_loop),
                        composition_loop,
                    )
                except BaseException:
                    retry_future = None
                if retry_future is not None:
                    while not retry_future.done():
                        if composition_loop.is_closed() or not composition_loop.is_running():
                            retry_future.cancel()
                            break
                        woke.wait(0.1)
                        woke.clear()
                    if retry_future.done() and not retry_future.cancelled():
                        with contextlib.suppress(BaseException):
                            retry_future.exception()
                        return

            # ``asyncio.run`` owns a fresh loop and therefore avoids awaiting
            # any Task tied to the destroyed composition loop.  The disposal
            # routine remains fail-closed if those foreign resources are still
            # pending and simply keeps provider ownership fenced.
            with contextlib.suppress(BaseException):
                asyncio.run(_finish_daytona_disposal(inventory, dispatcher, composition_loop))
        finally:
            _COMPOSITION_DISPOSAL_MONITORS.discard(current_thread())

    monitor = Thread(target=retry, name="fleet-daytona-composition-disposal-monitor", daemon=True)
    _COMPOSITION_DISPOSAL_MONITORS.add(monitor)
    monitor.start()


def _start_composition_disposal_fallback(
    inventory: RuntimeInventory,
    dispatcher: SyncBridgeDispatcher,
    composition_loop: asyncio.AbstractEventLoop | None,
) -> None:
    """Start disposal on an independent loop when no owner loop remains."""

    def dispose() -> None:
        try:
            asyncio.run(_finish_daytona_disposal(inventory, dispatcher, composition_loop))
        except BaseException as exc:
            logger.warning(
                "deferred Daytona composition fallback failed",
                extra={"error_type": type(exc).__name__},
            )
        finally:
            _COMPOSITION_DISPOSAL_MONITORS.discard(current_thread())

    monitor = Thread(target=dispose, name="fleet-daytona-composition-disposal-fallback", daemon=True)
    _COMPOSITION_DISPOSAL_MONITORS.add(monitor)
    try:
        monitor.start()
    except BaseException as exc:
        _COMPOSITION_DISPOSAL_MONITORS.discard(monitor)
        logger.critical(
            "unable to retain deferred Daytona composition disposal",
            extra={"error_type": type(exc).__name__},
        )


async def run_deferred_memory_outbox_reconcile(
    reconciler: MemoryOutboxReconciler,
    *,
    interval_seconds: float = 60.0,
) -> None:
    """Periodic outbox sweeps; never blocks startup readiness (P23/QRE-166)."""
    while True:
        try:
            receipt = await reconciler.reconcile_once()
        except Exception as exc:
            logger.warning(
                "Memory outbox reconcile sweep failed (%s); next interval retries",
                type(exc).__name__,
                exc_info=exc,
            )
        else:
            if receipt.claimed:
                logger.info(
                    "Memory outbox reconcile sweep claimed=%d promoted=%d dropped=%d retried=%d "
                    "dead_lettered=%d workspaces=%d provider_unavailable=%s",
                    receipt.claimed,
                    receipt.promoted,
                    receipt.dropped,
                    receipt.retried,
                    receipt.dead_lettered,
                    receipt.workspaces,
                    receipt.provider_unavailable,
                )
        await asyncio.sleep(interval_seconds)


async def _reconcile_daytona_settling(
    run_state: SettlingRunStateStore,
    session_manager: RuntimeSessionManager,
    *,
    fence_timeout: float = _STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> ReconciliationSummary:
    """Reconcile stale settling turns using bounded session fencing."""

    async def bounded_fence(session_id: UUID) -> None:
        remaining = fence_timeout
        if deadline is not None:
            remaining = min(remaining, deadline - asyncio.get_running_loop().time())
        if remaining <= 0:
            raise TimeoutError("startup recovery budget exhausted")
        fence_deadline = asyncio.get_running_loop().time() + remaining
        fence = session_manager.fence_session
        try:
            accepts_deadline = "deadline" in inspect.signature(fence).parameters
        except (TypeError, ValueError):
            accepts_deadline = False
        await asyncio.wait_for(
            fence(session_id, deadline=fence_deadline) if accepts_deadline else fence(session_id),
            timeout=remaining,
        )

    return await run_state.reconcile_settling(bounded_fence, deadline=deadline)


async def run_deferred_orphan_cleanup(
    gateway: Any,
    *,
    workspace_id: UUID,
    paths: Any,
    artifact_catalog: Any,
    grace_period: timedelta = timedelta(hours=1),
) -> None:
    """Best-effort orphan sweep that must never block startup readiness.

    Runs as a tracked background task after the composition is installed; its
    sandbox creation (cold provisioning) and deletions are deliberately kept off
    the readiness-critical path. Failures and timeouts are logged and left for a
    later startup.
    """
    from fleet_rlm.composition.daytona_workspace import OrphanCleanupReport, cleanup_orphan_bytes

    committed_storage_refs = await artifact_catalog.list_storage_refs(workspace_id=workspace_id)
    completed_runs = await artifact_catalog.list_completed_runs(workspace_id=workspace_id)
    active_runs = await artifact_catalog.list_active_runs(workspace_id=workspace_id)
    if not committed_storage_refs and not completed_runs and not active_runs:
        # The fresh startup sweep has no durable candidates. Do not provision
        # an ephemeral sandbox just to discover an empty Volume; that extra
        # mount call races the first Turn's Volume creation/readiness work.
        cleanup_report = OrphanCleanupReport(scanned=0, removed=0, retained=0, skipped_fresh=0)
        logger.info(
            "Daytona orphan cleanup complete phase=orphan_cleanup scanned=0 removed=0 retained=0 "
            "skipped_fresh=0 deferred=true"
        )
        return
    try:
        async with asyncio.timeout(_ORPHAN_CLEANUP_TIMEOUT_SECONDS):
            cleanup_report = await cleanup_orphan_bytes(
                gateway,
                workspace_id=workspace_id,
                paths=paths,
                committed_storage_refs=committed_storage_refs,
                completed_runs=completed_runs,
                active_runs=active_runs,
                grace_period=grace_period,
            )
    except TimeoutError:
        logger.warning(
            "Daytona orphan cleanup timed out phase=orphan_cleanup timeout_seconds=%.3f; left for a later startup",
            _ORPHAN_CLEANUP_TIMEOUT_SECONDS,
        )
        return
    except Exception:
        logger.warning("Daytona orphan cleanup failed phase=orphan_cleanup", exc_info=True)
        return
    logger.info(
        "Daytona orphan cleanup complete phase=orphan_cleanup scanned=%d removed=%d retained=%d "
        "skipped_fresh=%d deferred=true",
        cleanup_report.scanned,
        cleanup_report.removed,
        cleanup_report.retained,
        cleanup_report.skipped_fresh,
    )


async def build_daytona_composition(
    settings: Settings,
    *,
    skill_catalog: SkillCatalog,
    dispatcher: SyncBridgeDispatcher | None = None,
) -> RuntimeInventory:
    """Construct the Daytona runtime inventory; clean up partial init on failure."""
    from fleet_rlm.rlm._dspy_compat import assert_dspy_version

    assert_dspy_version()
    require_daytona_settings(settings)

    from fleet_rlm.api.local_scope import LocalScope
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.artifacts.workspace_storage import WorkspaceArtifactBlobGateway
    from fleet_rlm.attachments.lifecycle import AttachmentLifecycleService
    from fleet_rlm.attachments.local_catalog import WorkspaceAttachmentBlobGateway
    from fleet_rlm.attachments.paths import WorkspaceAttachmentPathPolicy
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.composition.daytona_environment import build_run_preparation, resolve_settings
    from fleet_rlm.composition.daytona_workspace import (
        DaytonaWorkspaceGateway,
        DaytonaWorkspaceVolumeGateway,
    )
    from fleet_rlm.daytona.provisioning import sandbox_spec_from_settings
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
    from fleet_rlm.persistence.repositories import (
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
        SqlAlchemyRunStateStore,
        SqlAlchemySandboxBindingStore,
        SqlAlchemySessionCatalog,
    )
    from fleet_rlm.rlm.program import RLMFactory, build_model_bundle
    from fleet_rlm.rlm.runtime import RLMRunner
    from fleet_rlm.runtime.cleanup import RunCleanupSupervisor
    from fleet_rlm.workspace.paths import volume_paths_from_settings
    from fleet_rlm.workspace.workspace import WorkspaceAccessGateway, WorkspaceFileService

    resolved = resolve_settings(settings)
    require_daytona_settings(resolved)
    sandbox_spec = sandbox_spec_from_settings(resolved)
    engine = create_async_engine_from_url(resolved.database_url or "")
    database_lifecycle: RuntimeDatabaseLifecycle | None = None
    resources: DaytonaRuntimeResources | None = None
    gateway: object | None = None
    orphan_cleanup_task: asyncio.Task[None] | None = None
    memory_outbox_task: asyncio.Task[None] | None = None
    try:
        # Fail closed on an unreachable or non-head database, inside the
        # cleanup scope so the engine above is always disposed on failure.
        await ensure_database_compatible(
            resolved.database_url or "",
            repo_root=Path(__file__).resolve().parents[3],
        )
        session_factory = create_session_factory(engine)
        database_lifecycle = RuntimeDatabaseLifecycle(engine=engine, session_factory=session_factory)
        cleanup = RunCleanupSupervisor(max_jobs=8)
        bindings = SqlAlchemySandboxBindingStore(session_factory)
        model_bundle = build_model_bundle(resolved)
        resources = DaytonaRuntimeResources(
            resolved,
            bindings=bindings,
            cleanup=cleanup,
            sandbox_spec=sandbox_spec,
            max_active_leases=resolved.max_active_daytona_leases,
            execution_output_cap=resolved.rlm_max_execution_output_chars,
            execution_timeout_s=resolved.rlm_execution_timeout_s,
            dispatcher=dispatcher,
        )
        mounted_workspace_gateway = DaytonaWorkspaceGateway(
            platform=resources.platform,
            volume_client=resources.volume_client,
            volume_config=resources.volume_config,
            sandbox_spec=resources.sandbox_spec,
            max_file_bytes=resolved.max_upload_bytes,
        )
        gateway = DaytonaWorkspaceVolumeGateway(
            mounted_workspace_gateway,
            mount_path=resolved.volume_mount_path,
        )
        volume_paths = volume_paths_from_settings(resolved)
        attachment_lifecycle = AttachmentLifecycleService(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=WorkspaceAttachmentBlobGateway(gateway),
            paths=WorkspaceAttachmentPathPolicy(volume_paths),
            max_bytes=resolved.max_upload_bytes,
        )
        artifact_catalog = SqlAlchemyArtifactCatalog(session_factory)
        artifact_reader = ArtifactReader(
            catalog=artifact_catalog,
            blobs=WorkspaceArtifactBlobGateway(gateway),
        )
        workspace_file_service = WorkspaceFileService(cast(WorkspaceAccessGateway, mounted_workspace_gateway))
        local_scope = LocalScope()
        startup_started = asyncio.get_running_loop().time()
        startup_deadline = startup_started + _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS
        session_runtime_registry = SessionRLMRegistry(idle_timeout=DEFAULT_IDLE_STOP_SECONDS)
        run_preparation = build_run_preparation(
            resources,
            attachment_lifecycle=attachment_lifecycle,
            skill_catalog=skill_catalog,
            settings=resolved,
            models=model_bundle,
            session_runtime_registry=session_runtime_registry,
        )
        run_state = SqlAlchemyRunStateStore(
            session_factory,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
        memory_outbox = SqlAlchemyMemoryPromotionOutbox(session_factory)
        lifecycle = RunLifecycleService(
            run_state,
            max_artifact_bytes=resolved.max_artifact_bytes,
            heartbeat_seconds=resolved.run_heartbeat_seconds,
            stale_after_seconds=resolved.run_stale_after_seconds,
            cleanup=cleanup,
            memory_outbox=memory_outbox,
        )
        recovery = await _reconcile_daytona_settling(
            run_state,
            resources.session_manager,
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
            from fleet_rlm.workspace.storage import AgentStorageSession, WorkspaceMemoryStorage

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
                    memory_session = AgentStorageSession(
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

        runner = RLMRunner(
            factory=RLMFactory(verbose=resolved.rlm_verbose),
            runtime_registry=session_runtime_registry,
        )
        coordinator = TurnRuntime(
            lifecycle=lifecycle,
            preparation=run_preparation,
            runner=runner,
            turn_timeout_seconds=resolved.turn_timeout_seconds,
            cleanup=cleanup,
            claim_loss_fence=resources.session_manager.fence_session,
            mlflow_tracing_enabled=resolved.mlflow_tracing_enabled,
            mlflow_expose_trace_id=resolved.mlflow_expose_trace_id,
        )
        return RuntimeInventory(
            run_environment_resources=resources,
            bridge_dispatcher=dispatcher,
            turn_coordinator=coordinator,
            runner=runner,
            session_catalog=session_catalog,
            run_lifecycle=lifecycle,
            attachment_lifecycle=attachment_lifecycle,
            artifact_reader=artifact_reader,
            workspace_volume_gateway=gateway,
            workspace_file_service=workspace_file_service,
            run_cleanup_supervisor=cleanup,
            run_preparation=run_preparation,
            run_state_store=run_state,
            database=database_lifecycle,
            model_bundle=model_bundle,
            session_runtime_registry=session_runtime_registry,
            orphan_cleanup_task=orphan_cleanup_task,
            memory_outbox_task=memory_outbox_task,
        )
    except BaseException:
        await _cancel_orphan_cleanup(orphan_cleanup_task)
        await _cancel_orphan_cleanup(memory_outbox_task)
        if resources is None and database_lifecycle is None:
            await engine.dispose()
        else:
            await _dispose_components(
                resources=resources,
                gateway=gateway,
                database=database_lifecycle,
                suppress_errors=True,
            )
        raise


async def install_daytona_composition(
    app: FastAPI,
    settings: Settings,
) -> RuntimeInventory:
    """Install the Daytona runtime inventory on the application."""
    skill_catalog = getattr(app.state, "skill_catalog", None)
    if not isinstance(skill_catalog, SkillCatalog):
        raise CompositionError("bundled Skill catalog is unavailable")
    # The composition loop owns every loop-affine Daytona SDK object and never
    # performs nested synchronous waits; bridges post SDK coroutines here.
    # QRE-154: each composition owns its dispatcher so overlapping app/test
    # compositions cannot overwrite each other's bridge authority.
    dispatcher = SyncBridgeDispatcher()
    composition_loop = asyncio.get_running_loop()
    dispatcher.set_loop(composition_loop)
    try:
        inventory = await build_daytona_composition(settings, skill_catalog=skill_catalog, dispatcher=dispatcher)
    except BaseException:
        dispatcher.clear_loop(composition_loop)
        raise
    try:
        from fleet_rlm.config import _CONFIG_PATH, active_profile
        from fleet_rlm.config_policy import ConfigPolicyService

        # Replace only the restart-facing policy service; keep every other
        # already-built inventory member to avoid field-by-field drift.
        inventory = replace(
            inventory,
            config_policy=ConfigPolicyService(
                _CONFIG_PATH,
                active_profile=active_profile(settings),
            ),
        )
        return install_runtime_inventory(app, inventory)
    except BaseException:
        clear_runtime_inventory(app)
        await _cancel_orphan_cleanup(inventory.orphan_cleanup_task)
        await _cancel_orphan_cleanup(getattr(inventory, "memory_outbox_task", None))
        await _dispose_components(
            resources=inventory.run_environment_resources,
            gateway=inventory.workspace_volume_gateway,
            database=inventory.database,
            suppress_errors=True,
        )
        dispatcher.clear_loop(composition_loop)
        raise


async def dispose_daytona_composition(app: FastAPI) -> None:
    """Dispose Daytona resources while preserving ownership and cleanup order."""
    inventory = clear_runtime_inventory(app)
    if inventory is None:
        return
    errors: list[BaseException] = []
    phase_failed = object()

    async def phase(awaitable: Any) -> Any:
        try:
            return await awaitable
        except BaseException as exc:
            errors.append(exc)
            return phase_failed

    # Stop accepting detached work first, but never let one cleanup hook skip
    # runtime fencing or provider retirement.
    await phase(_cancel_orphan_cleanup(getattr(inventory, "orphan_cleanup_task", None)))
    await phase(_cancel_orphan_cleanup(getattr(inventory, "memory_outbox_task", None)))
    cleanup = getattr(inventory, "run_cleanup_supervisor", None)
    if cleanup is not None:
        await phase(cleanup.shutdown(drain_seconds=30))
    runner = getattr(inventory, "runner", None)
    close_runner = getattr(runner, "aclose", None)
    if callable(close_runner):
        await phase(close_runner(drain_seconds=30))

    runtime_registry = getattr(inventory, "session_runtime_registry", None)
    deferred_settled = not errors
    if runtime_registry is not None:
        shutdown_result = await phase(runtime_registry.shutdown(drain_seconds=30))
        if shutdown_result is phase_failed:
            deferred_settled = False
            logger.warning("Session runtime shutdown reported an error; provider ownership is retained")
        wait_deferred = getattr(runtime_registry, "wait_deferred_closes", None)
        if callable(wait_deferred):
            result = await phase(wait_deferred(timeout=30))
            if result is phase_failed or result is False:
                deferred_settled = False

    preparation = getattr(inventory, "run_preparation", None)
    close_preparation = getattr(preparation, "aclose", None)
    if callable(close_preparation):
        result = await phase(close_preparation())
        if result is phase_failed or result is False:
            deferred_settled = False

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
                resources=getattr(inventory, "run_environment_resources", None),
                gateway=getattr(inventory, "workspace_volume_gateway", None),
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
        if composition_loop is not None and not composition_loop.is_closed():
            deferred = asyncio.create_task(
                _finish_daytona_disposal(inventory, dispatcher, composition_loop),
                name="fleet-daytona-composition-disposal",
            )
            _retain_composition_disposal(
                deferred,
                inventory=inventory,
                dispatcher=dispatcher,
                composition_loop=composition_loop,
            )
        else:
            # The lifespan loop may already have been torn down after a
            # cancellation/error phase.  Keep the provider fenced and move
            # the retry to an independent owner instead of losing it silently.
            _start_composition_disposal_fallback(inventory, dispatcher, composition_loop)
    elif isinstance(dispatcher, SyncBridgeDispatcher):
        dispatcher.clear_loop(dispatcher.service_loop())

    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("Daytona composition disposal failed", errors)
