"""Daytona runtime composition and process-lifetime resource ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from fleet_rlm.composition.common import CompositionError, clear_composition_state
from fleet_rlm.config import Settings


def require_daytona_settings(settings: Settings) -> None:
    """Fail closed when the Daytona runtime inventory is incomplete."""
    if settings.run_environment != "daytona":
        raise CompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
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


async def _dispose_components(
    *,
    resources: Any | None,
    gateway: Any | None,
    suppress_errors: bool,
) -> None:
    first_error: Exception | None = None
    for target, method_name in ((gateway, "close"), (resources, "adispose")):
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

    from fleet_rlm.artifacts.daytona_catalog import DaytonaArtifactBlobGateway
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.daytona.paths import volume_paths_from_settings
    from fleet_rlm.daytona.run_environment import LiveKernelResources, resolve_settings
    from fleet_rlm.daytona.workspace_volume import create_daytona_workspace_volume_gateway
    from fleet_rlm.files.lifecycle import AttachmentModule
    from fleet_rlm.files.local_catalog import WorkspaceAttachmentBlobGateway
    from fleet_rlm.files.paths import DaytonaAttachmentPathPolicy
    from fleet_rlm.observability.exporters import LoggingTurnExporter
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
    engine = create_async_engine_from_url(resolved.database_url or "")
    resources: Any | None = None
    gateway: Any | None = None
    try:
        session_factory = create_session_factory(engine)
        resources = LiveKernelResources(resolved, session_factory=session_factory, engine=engine)
        if resolved.daytona_api_key is None:
            raise CompositionError("Daytona composition missing required settings: FLEET_DAYTONA_API_KEY")
        gateway = create_daytona_workspace_volume_gateway(
            api_key=resolved.daytona_api_key.get_secret_value(),
            volume_name=resolved.volume_name,
            mount_path=resolved.volume_mount_path,
        )
        attachment_lifecycle = AttachmentModule(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=WorkspaceAttachmentBlobGateway(gateway),
            paths=DaytonaAttachmentPathPolicy(volume_paths_from_settings(resolved)),
            max_bytes=resolved.max_upload_bytes,
        )
        artifact_reader = ArtifactReader(
            catalog=SqlAlchemyArtifactCatalog(session_factory),
            blobs=DaytonaArtifactBlobGateway(gateway),
        )
        resources.configure_preparation(attachment_lifecycle)
        turn_state = SqlAlchemyTurnStateStore(
            session_factory,
            stale_after_seconds=resolved.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
        lifecycle = TurnLifecycleModule(
            turn_state,
            max_artifact_bytes=resolved.max_artifact_bytes,
            heartbeat_seconds=resolved.run_heartbeat_seconds,
        )
        coordinator = TurnCoordinator(
            lifecycle=lifecycle,
            preparation=resources,
            runner=RLMRunner(factory=RLMFactory()),
        )
        _ = LoggingTurnExporter()
        return DaytonaCompositionHandles(
            resources=resources,
            turn_coordinator=coordinator,
            session_catalog=session_catalog,
            turn_lifecycle=lifecycle,
            attachment_lifecycle=attachment_lifecycle,
            artifact_reader=artifact_reader,
            workspace_volume_gateway=gateway,
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
        handles.resources.skill_registry = getattr(app.state, "skill_registry", None)
        handles.resources.capability_registry = getattr(app.state, "capability_registry", None)
        app.state.composition_ready = True
        app.state.run_environment_resources = handles.resources
        app.state.db_engine = handles.resources._engine  # noqa: SLF001
        app.state.session_catalog = handles.session_catalog
        app.state.turn_lifecycle = handles.turn_lifecycle
        app.state.turn_coordinator = handles.turn_coordinator
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
        await _dispose_components(
            resources=getattr(app.state, "run_environment_resources", None),
            gateway=getattr(app.state, "workspace_volume_gateway", None),
            suppress_errors=False,
        )
    finally:
        clear_composition_state(app)
