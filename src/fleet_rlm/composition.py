"""Live FastAPI composition inventory (B9).

Importing this module must not require credentials or construct clients.
Construction happens only via ``install_live_composition`` when live mode is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fleet_rlm.config import Settings
from fleet_rlm.rlm.budgets import RunBudget


class LiveCompositionError(RuntimeError):
    """Raised when live composition cannot be assembled (fail closed)."""


def require_live_settings(settings: Settings) -> None:
    """Fail closed when required live deps are missing. Credentials alone are not enough."""
    if settings.run_environment != "daytona":
        raise LiveCompositionError("Daytona composition requires run_environment='daytona'")
    missing: list[str] = []
    if settings.daytona_api_key is None or not settings.daytona_api_key.get_secret_value().strip():
        missing.append("FLEET_DAYTONA_API_KEY")
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value().strip():
        missing.append("FLEET_LLM_API_KEY")
    if not (settings.database_url or "").strip():
        missing.append("FLEET_DATABASE_URL")
    if settings.auth_mode == "neon" and not (settings.neon_auth_url or "").strip():
        missing.append("FLEET_NEON_AUTH_URL")
    if missing:
        raise LiveCompositionError("live composition missing required settings: " + ", ".join(missing))


@dataclass(slots=True)
class LiveCompositionHandles:
    """Process-owned live handles disposed on shutdown."""

    resources: Any
    turn_coordinator: Any
    session_catalog: Any
    turn_lifecycle: Any
    attachment_lifecycle: Any
    artifact_reader: Any
    workspace_volume_gateway: Any


@dataclass(slots=True)
class OfflineCompositionHandles:
    """Hermetic process-owned adapters for local development and tests."""

    turn_coordinator: Any
    attachment_lifecycle: Any
    artifact_reader: Any
    workspace_volume_mirror: Any
    session_catalog: Any
    turn_lifecycle: Any


async def _dispose_live_components(
    *,
    resources: Any | None,
    gateway: Any | None,
    suppress_errors: bool,
) -> None:
    """Attempt every application-lifetime cleanup and optionally preserve an outer error."""
    first_error: Exception | None = None
    for target, method_name in ((gateway, "close"), (resources, "adispose")):
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            await method()
        except Exception as exc:  # noqa: BLE001 - all handles must still be attempted
            if first_error is None:
                first_error = exc
    if first_error is not None and not suppress_errors:
        raise first_error


def _host_roots(settings: Settings) -> tuple[str, str]:
    data_root = Path(settings.data_root)
    upload_root = str(data_root / "attachments")
    artifact_root = str(data_root / "artifacts")
    return upload_root, artifact_root


def install_offline_composition(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> OfflineCompositionHandles:
    """Build hermetic adapters once during lifespan; routes never construct them."""
    from fleet_rlm.artifacts.daytona_catalog import DaytonaArtifactBlobGateway
    from fleet_rlm.artifacts.local_catalog import (
        LocalArtifactBlobGateway,
        LocalArtifactCatalog,
        LocalArtifactReaderCatalog,
    )
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.chat.hermetic_run_environment import HermeticRLMFactory, HermeticTurnPreparation
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
    from fleet_rlm.daytona.paths import volume_paths_from_settings
    from fleet_rlm.daytona.volume_fs import HostVolumeMirror
    from fleet_rlm.daytona.workspace_volume import OfflineHostVolumeGateway
    from fleet_rlm.files.lifecycle import AttachmentModule
    from fleet_rlm.files.local_catalog import (
        LocalAttachmentBlobGateway,
        LocalAttachmentCatalog,
        WorkspaceAttachmentBlobGateway,
    )
    from fleet_rlm.files.paths import DaytonaAttachmentPathPolicy, LocalAttachmentPathPolicy
    from fleet_rlm.persistence.repositories import (
        InMemorySessionCatalog,
        InMemoryTurnStateStore,
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
        SqlAlchemySessionCatalog,
        SqlAlchemyTurnStateStore,
    )
    from fleet_rlm.rlm.runner import RLMRunner

    upload_root, artifact_root = _host_roots(settings)
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(settings),
    )
    volume_gateway = OfflineHostVolumeGateway(mirror)
    if session_factory is None:
        attachment_lifecycle: Any = AttachmentModule(
            catalog=LocalAttachmentCatalog(upload_root),
            blobs=LocalAttachmentBlobGateway(Path(upload_root)),
            paths=LocalAttachmentPathPolicy(Path(upload_root)),
            max_bytes=settings.max_upload_bytes,
        )
        artifact_catalog = LocalArtifactCatalog(
            artifact_root,
            max_bytes=settings.max_artifact_bytes,
            volume_paths=mirror.volume_paths,
        )
        artifact_reader: Any = ArtifactReader(
            catalog=LocalArtifactReaderCatalog(artifact_catalog),
            blobs=LocalArtifactBlobGateway(artifact_catalog),
        )
    else:
        attachment_lifecycle = AttachmentModule(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=WorkspaceAttachmentBlobGateway(volume_gateway),
            paths=DaytonaAttachmentPathPolicy(mirror.volume_paths),
            max_bytes=settings.max_upload_bytes,
        )
        artifact_reader = ArtifactReader(
            catalog=SqlAlchemyArtifactCatalog(session_factory),
            blobs=DaytonaArtifactBlobGateway(volume_gateway),
        )
    if session_factory is None:
        turn_state = InMemoryTurnStateStore()
        session_catalog = InMemorySessionCatalog(turn_state)
    else:
        turn_state = SqlAlchemyTurnStateStore(
            session_factory,
            stale_after_seconds=settings.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
    lifecycle = TurnLifecycleModule(
        turn_state,
        max_artifact_bytes=settings.max_artifact_bytes,
        heartbeat_seconds=settings.run_heartbeat_seconds,
    )
    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=HermeticTurnPreparation(
            attachments=attachment_lifecycle,
            budget=RunBudget(
                max_iterations=settings.budget_max_iterations,
                max_llm_calls=settings.budget_max_llm_calls,
                max_output_chars=settings.budget_max_output_chars,
                max_wall_seconds=settings.budget_max_wall_seconds,
                max_sub_lm_concurrency=settings.budget_max_sub_lm_concurrency,
                max_tool_calls=settings.budget_max_tool_calls,
                max_skill_loads=settings.budget_max_skill_loads,
            ),
        ),
        runner=RLMRunner(factory=HermeticRLMFactory()),
    )
    handles = OfflineCompositionHandles(
        turn_coordinator=coordinator,
        attachment_lifecycle=attachment_lifecycle,
        artifact_reader=artifact_reader,
        workspace_volume_mirror=mirror,
        session_catalog=session_catalog,
        turn_lifecycle=lifecycle,
    )
    app.state.turn_coordinator = coordinator
    app.state.turn_lifecycle = lifecycle
    app.state.turn_state_store = turn_state
    app.state.session_catalog = session_catalog
    app.state.attachment_lifecycle = attachment_lifecycle
    app.state.artifact_reader = artifact_reader
    app.state.workspace_volume_mirror = mirror
    app.state.composition_ready = True
    return handles


async def build_live_composition(settings: Settings) -> LiveCompositionHandles:
    """Construct the live lifespan inventory and clean partial failures."""
    require_live_settings(settings)

    from fleet_rlm.artifacts.daytona_catalog import DaytonaArtifactBlobGateway
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.daytona.paths import volume_paths_from_settings
    from fleet_rlm.daytona.run_environment import LiveKernelResources, resolve_settings
    from fleet_rlm.daytona.workspace_volume import create_daytona_workspace_volume_gateway
    from fleet_rlm.files.lifecycle import AttachmentModule
    from fleet_rlm.files.local_catalog import WorkspaceAttachmentBlobGateway
    from fleet_rlm.files.paths import DaytonaAttachmentPathPolicy
    from fleet_rlm.observability.exporters import LoggingTurnExporter
    from fleet_rlm.persistence.database import (
        create_async_engine_from_url,
        create_session_factory,
    )
    from fleet_rlm.persistence.repositories import (
        SqlAlchemyArtifactCatalog,
        SqlAlchemyAttachmentCatalog,
    )
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.runner import RLMRunner

    resolved = resolve_settings(settings)
    require_live_settings(resolved)

    engine = create_async_engine_from_url(resolved.database_url or "")
    resources: Any | None = None
    gateway: Any | None = None
    try:
        session_factory = create_session_factory(engine)
        resources = LiveKernelResources(
            resolved,
            session_factory=session_factory,
            engine=engine,
        )
        volume_paths = volume_paths_from_settings(resolved)
        if resolved.daytona_api_key is None:
            raise LiveCompositionError("live composition missing required settings: FLEET_DAYTONA_API_KEY")
        gateway = create_daytona_workspace_volume_gateway(
            api_key=resolved.daytona_api_key.get_secret_value(),
            volume_name=resolved.volume_name,
            mount_path=resolved.volume_mount_path,
        )
        attachment_lifecycle = AttachmentModule(
            catalog=SqlAlchemyAttachmentCatalog(session_factory),
            blobs=WorkspaceAttachmentBlobGateway(gateway),
            paths=DaytonaAttachmentPathPolicy(volume_paths),
            max_bytes=resolved.max_upload_bytes,
        )
        artifact_reader = ArtifactReader(
            catalog=SqlAlchemyArtifactCatalog(session_factory),
            blobs=DaytonaArtifactBlobGateway(gateway),
        )
        resources.configure_preparation(attachment_lifecycle)

        runner = RLMRunner(factory=RLMFactory())
        from fleet_rlm.chat.turn_lifecycle import TurnLifecycleModule
        from fleet_rlm.persistence.repositories import (
            SqlAlchemySessionCatalog,
            SqlAlchemyTurnStateStore,
        )

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
            runner=runner,
        )
        _ = LoggingTurnExporter()

        return LiveCompositionHandles(
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
            await _dispose_live_components(
                resources=resources,
                gateway=gateway,
                suppress_errors=True,
            )
        raise


async def install_live_composition(app: FastAPI, settings: Settings) -> LiveCompositionHandles:
    """Attach an already-migrated live inventory to ``app.state``."""
    handles = await build_live_composition(settings)
    try:
        skill_registry = getattr(app.state, "skill_registry", None)
        handles.resources.skill_registry = skill_registry
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

        if settings.auth_mode == "neon":
            from fleet_rlm.api.neon_auth import NeonAuthVerifier

            app.state.auth_verifier = NeonAuthVerifier(neon_auth_url=settings.neon_auth_url or "")

        return handles
    except Exception:
        app.state.composition_ready = False
        await _dispose_live_components(
            resources=handles.resources,
            gateway=handles.workspace_volume_gateway,
            suppress_errors=True,
        )
        raise


async def dispose_live_composition(app: FastAPI) -> None:
    """Best-effort shutdown of live resources."""
    resources = getattr(app.state, "run_environment_resources", None)
    gateway = getattr(app.state, "workspace_volume_gateway", None)
    try:
        await _dispose_live_components(
            resources=resources,
            gateway=gateway,
            suppress_errors=False,
        )
    finally:
        app.state.composition_ready = False


__all__ = [
    "LiveCompositionError",
    "LiveCompositionHandles",
    "OfflineCompositionHandles",
    "build_live_composition",
    "dispose_live_composition",
    "install_live_composition",
    "install_offline_composition",
    "require_live_settings",
]
