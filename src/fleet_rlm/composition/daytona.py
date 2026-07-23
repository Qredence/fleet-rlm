"""Daytona runtime composition and process-lifetime resource ownership."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from fleet_rlm.composition.common import CompositionError, clear_composition_state
from fleet_rlm.config import Settings
from fleet_rlm.skills.catalog import build_bundled_skill_catalog


def require_daytona_settings(settings: Settings) -> None:
    """Fail closed when the Daytona runtime inventory is incomplete."""
    if settings.run_environment != "daytona":
        raise CompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
    if not (settings.daytona_snapshot or "").strip():
        missing.append("FLEET_DAYTONA_SNAPSHOT")
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value().strip():
        missing.append("FLEET_LLM_API_KEY")
    if not (settings.database_url or "").strip():
        missing.append("FLEET_DATABASE_URL")
    if missing:
        raise CompositionError("Daytona composition missing required settings: " + ", ".join(missing))


@dataclass(slots=True)
class DaytonaCompositionHandles:
    resources: Any
    turn_coordinator: Any
    session_catalog: Any
    turn_lifecycle: Any
    attachment_lifecycle: Any
    artifact_reader: Any
    workspace_volume_gateway: Any
    turn_cleanup_supervisor: Any = None
    turn_preparation: Any = None


async def _dispose_components(
    *,
    resources: Any | None,
    gateway: Any | None,
    suppress_errors: bool,
) -> None:
    first_error: Exception | None = None
    for target, method_name in ((resources, "adispose"), (gateway, "close")):
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            await method()
        except Exception as exc:  # noqa: BLE001 - every handle must be attempted
            if first_error is None:
                first_error = exc
    if first_error is not None and not suppress_errors:
        raise first_error


async def build_daytona_composition(settings: Settings) -> DaytonaCompositionHandles:
    """Construct the Daytona lifespan inventory and clean partial failures."""
    from fleet_rlm.rlm.dspy_contract import assert_dspy_version

    assert_dspy_version()
    require_daytona_settings(settings)

    from fleet_rlm.api.local_scope import LocalScope
    from fleet_rlm.artifacts.daytona_catalog import DaytonaArtifactBlobGateway
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.daytona.orphan_cleanup import cleanup_orphan_bytes
    from fleet_rlm.daytona.paths import volume_paths_from_settings
    from fleet_rlm.daytona.run_environment import LiveKernelResources, build_turn_preparation, resolve_settings
    from fleet_rlm.daytona.sandbox_spec import sandbox_spec_from_settings
    from fleet_rlm.daytona.workspace_volume import create_daytona_workspace_volume_gateway
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService
    from fleet_rlm.files.local_catalog import WorkspaceAttachmentBlobGateway
    from fleet_rlm.files.paths import DaytonaAttachmentPathPolicy
    from fleet_rlm.persistence.database import create_async_engine_from_url, create_session_factory
    from fleet_rlm.persistence.repositories import (
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
        SqlAlchemySessionCatalog,
        SqlAlchemyTurnStateStore,
    )
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.runner import RLMRunner

    resolved = resolve_settings(settings)
    require_daytona_settings(resolved)
    sandbox_spec = sandbox_spec_from_settings(resolved)
    engine = create_async_engine_from_url(resolved.database_url or "")
    resources: Any | None = None
    gateway: Any | None = None
    try:
        session_factory = create_session_factory(engine)
        cleanup = TurnCleanupSupervisor(max_jobs=8)
        resources = LiveKernelResources(
            resolved,
            session_factory=session_factory,
            engine=engine,
            sandbox_spec=sandbox_spec,
            cleanup=cleanup,
        )
        if resolved.daytona_api_key is None:
            raise CompositionError("Daytona composition missing required settings: FLEET_DAYTONA_API_KEY")
        gateway = create_daytona_workspace_volume_gateway(
            api_key=resolved.daytona_api_key.get_secret_value(),
            volume_name=resolved.volume_name,
            mount_path=resolved.volume_mount_path,
            sandbox_spec=sandbox_spec,
        )
        volume_paths = volume_paths_from_settings(resolved)
        attachment_lifecycle = AttachmentLifecycleService(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=WorkspaceAttachmentBlobGateway(gateway),
            paths=DaytonaAttachmentPathPolicy(volume_paths),
            max_bytes=resolved.max_upload_bytes,
        )
        artifact_catalog = SqlAlchemyArtifactCatalog(session_factory)
        artifact_reader = ArtifactReader(
            catalog=artifact_catalog,
            blobs=DaytonaArtifactBlobGateway(gateway),
        )
        local_scope = LocalScope()
        await cleanup_orphan_bytes(
            gateway,
            workspace_id=local_scope.workspace_id,
            paths=volume_paths,
            committed_storage_refs=await artifact_catalog.list_storage_refs(workspace_id=local_scope.workspace_id),
            completed_runs=await artifact_catalog.list_completed_runs(workspace_id=local_scope.workspace_id),
            active_runs=await artifact_catalog.list_active_runs(workspace_id=local_scope.workspace_id),
            grace_period=timedelta(hours=1),
        )
        turn_preparation = build_turn_preparation(
            resources,
            attachment_lifecycle=attachment_lifecycle,
            skill_catalog=build_bundled_skill_catalog(),
        )
        turn_state = SqlAlchemyTurnStateStore(
            session_factory,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
        lifecycle = TurnLifecycleService(
            turn_state,
            max_artifact_bytes=resolved.max_artifact_bytes,
            heartbeat_seconds=resolved.run_heartbeat_seconds,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        await turn_state.reconcile_settling(resources.session_manager.fence_session)
        coordinator = TurnCoordinator(
            lifecycle=lifecycle,
            preparation=turn_preparation,
            runner=RLMRunner(factory=RLMFactory()),
            turn_timeout_seconds=resolved.turn_timeout_seconds,
            cleanup=cleanup,
            claim_loss_fence=resources.session_manager.fence_session,
        )
        return DaytonaCompositionHandles(
            resources=resources,
            turn_coordinator=coordinator,
            session_catalog=session_catalog,
            turn_lifecycle=lifecycle,
            attachment_lifecycle=attachment_lifecycle,
            artifact_reader=artifact_reader,
            workspace_volume_gateway=gateway,
            turn_cleanup_supervisor=cleanup,
            turn_preparation=turn_preparation,
        )
    except Exception:
        if resources is None:
            await engine.dispose()
        else:
            await _dispose_components(resources=resources, gateway=gateway, suppress_errors=True)
        raise


async def install_daytona_composition(
    app: FastAPI,
    settings: Settings,
) -> DaytonaCompositionHandles:
    """Attach an already-migrated Daytona inventory to app state."""
    handles = await build_daytona_composition(settings)
    try:
        app.state.composition_ready = True
        app.state.run_environment_resources = handles.resources
        app.state.db_engine = handles.resources._engine  # noqa: SLF001
        app.state.session_catalog = handles.session_catalog
        app.state.turn_lifecycle = handles.turn_lifecycle
        app.state.turn_coordinator = handles.turn_coordinator
        app.state.turn_preparation = handles.turn_preparation
        app.state.turn_cleanup_supervisor = handles.turn_cleanup_supervisor
        app.state.attachment_lifecycle = handles.attachment_lifecycle
        app.state.artifact_reader = handles.artifact_reader
        app.state.workspace_volume_gateway = handles.workspace_volume_gateway
        app.state.session_manager = handles.resources.session_manager
        app.state.rlm_model_bundle = handles.resources.models
        return handles
    except Exception:
        clear_composition_state(app)
        await _dispose_components(
            resources=handles.resources,
            gateway=handles.workspace_volume_gateway,
            suppress_errors=True,
        )
        raise


async def dispose_daytona_composition(app: FastAPI) -> None:
    """Best-effort shutdown of Daytona resources."""
    try:
        cleanup = getattr(app.state, "turn_cleanup_supervisor", None)
        if cleanup is not None:
            await cleanup.shutdown(drain_seconds=30)
        await _dispose_components(
            resources=getattr(app.state, "run_environment_resources", None),
            gateway=getattr(app.state, "workspace_volume_gateway", None),
            suppress_errors=False,
        )
    finally:
        clear_composition_state(app)
