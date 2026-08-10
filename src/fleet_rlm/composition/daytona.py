"""Daytona runtime composition and process-lifetime resource ownership."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI

from fleet_rlm.composition.common import CompositionError
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
from fleet_rlm.persistence.database import ensure_database_compatible
from fleet_rlm.persistence.repositories.turns import ReconciliationSummary
from fleet_rlm.skills.catalog import SkillCatalog

logger = logging.getLogger(__name__)
_ORPHAN_CLEANUP_TIMEOUT_SECONDS = 60
_STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS = 15
_STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS = 75.0


def require_daytona_settings(settings: Settings) -> None:
    """
    Validate that all required Daytona runtime settings are configured.

    Raises:
        CompositionError: If the runtime environment is not Daytona or one or more required settings are missing.
    """
    if settings.run_environment != "daytona":
        raise CompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
    if not (settings.daytona_snapshot or "").strip():
        missing.append("FLEET_DAYTONA_SNAPSHOT")
    from fleet_rlm.rlm.lm_factory import has_llm_credentials, sanitize_base_url

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
) -> None:
    """
    Asynchronously disposes the available runtime components.

    Parameters:
        resources (RuntimeProcessResources | None): Runtime process resources to dispose.
        gateway (object | None): Gateway to close.
        database (RuntimeDatabaseLifecycle | None): Database lifecycle to close.
        suppress_errors (bool): Whether to suppress disposal errors.
    """
    first_error: Exception | None = None
    for target, method_name in ((resources, "adispose"), (gateway, "close"), (database, "aclose")):
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            await method()
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None and not suppress_errors:
        raise first_error


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


async def _reconcile_daytona_settling(
    run_state: SettlingRunStateStore,
    session_manager: RuntimeSessionManager,
    *,
    fence_timeout: float = _STARTUP_RECOVERY_FENCE_TIMEOUT_SECONDS,
    deadline: float | None = None,
) -> ReconciliationSummary:
    """
    Reconcile stale settling turns using bounded session fencing.

    Parameters:
        fence_timeout (float): Maximum time allowed to fence each session, in seconds.
        deadline (float | None): Optional monotonic-time deadline for the overall reconciliation.

    Returns:
        ReconciliationSummary: Summary of the reconciliation results.
    """

    async def bounded_fence(session_id: UUID) -> None:
        remaining = fence_timeout
        if deadline is not None:
            remaining = min(remaining, deadline - asyncio.get_running_loop().time())
        if remaining <= 0:
            raise TimeoutError("startup recovery budget exhausted")
        await asyncio.wait_for(session_manager.fence_session(session_id), timeout=remaining)

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
    from fleet_rlm.daytona.workspace_gateway import OrphanCleanupReport, cleanup_orphan_bytes

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


async def build_daytona_composition(settings: Settings, *, skill_catalog: SkillCatalog) -> RuntimeInventory:
    """
    Construct the Daytona runtime inventory and clean up partially initialized resources on failure.

    Parameters:
        settings (Settings): Application settings used to configure the Daytona runtime.
        skill_catalog (SkillCatalog): Application skill catalog used to build turn preparation.

    Returns:
        RuntimeInventory: The initialized Daytona runtime services and resources.
    """
    from fleet_rlm.rlm.dspy_contract import assert_dspy_version

    assert_dspy_version()
    require_daytona_settings(settings)

    from fleet_rlm.api.local_scope import LocalScope
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.artifacts.workspace_storage import WorkspaceArtifactBlobGateway
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.daytona.provisioning import sandbox_spec_from_settings
    from fleet_rlm.daytona.run_environment import DaytonaRuntimeResources, build_run_preparation, resolve_settings
    from fleet_rlm.daytona.workspace_gateway import (
        DaytonaWorkspaceGateway,
        DaytonaWorkspaceVolumeGateway,
    )
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService
    from fleet_rlm.files.local_catalog import WorkspaceAttachmentBlobGateway
    from fleet_rlm.files.paths import WorkspaceAttachmentPathPolicy
    from fleet_rlm.files.volume_paths import volume_paths_from_settings
    from fleet_rlm.files.workspace_access import WorkspaceFileService
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
    from fleet_rlm.persistence.repositories import (
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
        SqlAlchemyRunStateStore,
        SqlAlchemySandboxBindingStore,
        SqlAlchemySessionCatalog,
    )
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.lm_factory import build_model_bundle
    from fleet_rlm.rlm.runner import RLMRunner

    resolved = resolve_settings(settings)
    require_daytona_settings(resolved)
    sandbox_spec = sandbox_spec_from_settings(resolved)
    engine = create_async_engine_from_url(resolved.database_url or "")
    database_lifecycle: RuntimeDatabaseLifecycle | None = None
    resources: DaytonaRuntimeResources | None = None
    gateway: object | None = None
    orphan_cleanup_task: asyncio.Task[None] | None = None
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
        workspace_file_service = WorkspaceFileService(mounted_workspace_gateway)
        local_scope = LocalScope()
        startup_started = asyncio.get_running_loop().time()
        startup_deadline = startup_started + _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS
        run_preparation = build_run_preparation(
            resources,
            attachment_lifecycle=attachment_lifecycle,
            skill_catalog=skill_catalog,
            settings=resolved,
            models=model_bundle,
        )
        run_state = SqlAlchemyRunStateStore(
            session_factory,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
        lifecycle = RunLifecycleService(
            run_state,
            max_artifact_bytes=resolved.max_artifact_bytes,
            heartbeat_seconds=resolved.run_heartbeat_seconds,
            stale_after_seconds=resolved.run_stale_after_seconds,
            cleanup=cleanup,
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

        coordinator = TurnCoordinator(
            lifecycle=lifecycle,
            preparation=run_preparation,
            runner=RLMRunner(factory=RLMFactory(verbose=resolved.rlm_verbose)),
            turn_timeout_seconds=resolved.turn_timeout_seconds,
            cleanup=cleanup,
            claim_loss_fence=resources.session_manager.fence_session,
            mlflow_tracing_enabled=resolved.mlflow_tracing_enabled,
            mlflow_expose_trace_id=resolved.mlflow_expose_trace_id,
        )
        return RuntimeInventory(
            run_environment_resources=resources,
            turn_coordinator=coordinator,
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
            orphan_cleanup_task=orphan_cleanup_task,
        )
    except Exception:
        await _cancel_orphan_cleanup(orphan_cleanup_task)
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
    """Install the Daytona runtime inventory on the application.

    Parameters:
        app (FastAPI): Application that provides the skill catalog and receives the runtime inventory.
        settings (Settings): Configuration used to build the Daytona composition.

    Returns:
        RuntimeInventory: The installed Daytona runtime inventory.
    """
    from fleet_rlm.daytona.interpreter import set_bridge_service_loop

    skill_catalog = getattr(app.state, "skill_catalog", None)
    if not isinstance(skill_catalog, SkillCatalog):
        raise CompositionError("bundled Skill catalog is unavailable")
    # The composition loop owns every loop-affine Daytona SDK object and never
    # performs nested synchronous waits; bridges post SDK coroutines here.
    set_bridge_service_loop(asyncio.get_running_loop())
    try:
        inventory = await build_daytona_composition(settings, skill_catalog=skill_catalog)
    except Exception:
        set_bridge_service_loop(None)
        raise
    try:
        from fleet_rlm.config import _CONFIG_PATH, active_profile
        from fleet_rlm.config_policy import ConfigPolicyService

        inventory = RuntimeInventory(
            turn_coordinator=inventory.turn_coordinator,
            attachment_lifecycle=inventory.attachment_lifecycle,
            artifact_reader=inventory.artifact_reader,
            session_catalog=inventory.session_catalog,
            run_lifecycle=inventory.run_lifecycle,
            run_preparation=inventory.run_preparation,
            run_cleanup_supervisor=inventory.run_cleanup_supervisor,
            run_state_store=inventory.run_state_store,
            config_policy=ConfigPolicyService(
                _CONFIG_PATH,
                active_profile=active_profile(settings),
            ),
            database=inventory.database,
            model_bundle=inventory.model_bundle,
            run_environment_resources=inventory.run_environment_resources,
            workspace_volume_gateway=inventory.workspace_volume_gateway,
            workspace_file_service=inventory.workspace_file_service,
            workspace_volume_mirror=inventory.workspace_volume_mirror,
            orphan_cleanup_task=inventory.orphan_cleanup_task,
        )
        return install_runtime_inventory(app, inventory)
    except Exception:
        clear_runtime_inventory(app)
        await _cancel_orphan_cleanup(inventory.orphan_cleanup_task)
        await _dispose_components(
            resources=inventory.run_environment_resources,
            gateway=inventory.workspace_volume_gateway,
            database=inventory.database,
            suppress_errors=True,
        )
        set_bridge_service_loop(None)
        raise


async def dispose_daytona_composition(app: FastAPI) -> None:
    """Dispose the Daytona runtime composition and release its associated resources.

    The shutdown drains pending turn cleanup before disposing runtime, gateway, and
    database resources, then unregisters the Daytona bridge service loop.
    """
    from fleet_rlm.daytona.interpreter import set_bridge_service_loop

    inventory = clear_runtime_inventory(app)
    orphan_task = getattr(inventory, "orphan_cleanup_task", None)
    await _cancel_orphan_cleanup(orphan_task)
    cleanup = getattr(inventory, "run_cleanup_supervisor", None)
    if cleanup is not None:
        await cleanup.shutdown(drain_seconds=30)
    await _dispose_components(
        resources=inventory.run_environment_resources if inventory is not None else None,
        gateway=inventory.workspace_volume_gateway if inventory is not None else None,
        database=inventory.database if inventory is not None else None,
        suppress_errors=False,
    )
    # Release the bridge service loop after component disposal so bridges can
    # still run SDK coroutines while runtimes shut down.
    set_bridge_service_loop(None)
