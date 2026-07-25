"""Shared composition types and local inventory wiring."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from fleet_rlm.config import Settings
from fleet_rlm.rlm.dspy_contract import RLMOptions, assert_dspy_version


class CompositionError(RuntimeError):
    """Raised when a runtime composition cannot be assembled."""


COMPOSITION_STATE_FIELDS = (
    "artifact_reader",
    "config_policy",
    "attachment_lifecycle",
    "rlm_model_bundle",
    "run_environment_resources",
    "session_catalog",
    "session_manager",
    "turn_coordinator",
    "turn_cleanup_supervisor",
    "turn_lifecycle",
    "turn_preparation",
    "turn_state_store",
    "workspace_volume_gateway",
    "workspace_volume_mirror",
)


@dataclass(slots=True)
class LocalCompositionHandles:
    """Process-owned adapters for Deno and private tests."""

    turn_coordinator: Any
    attachment_lifecycle: Any
    artifact_reader: Any
    workspace_volume_mirror: Any
    session_catalog: Any
    turn_lifecycle: Any
    turn_cleanup_supervisor: Any


@dataclass(frozen=True, slots=True)
class LocalStorageAdapters:
    """Attachment and Artifact adapters shared by local runtime profiles."""

    attachment_lifecycle: Any
    artifact_reader: Any


def host_roots(settings: Settings) -> tuple[str, str]:
    data_root = Path(settings.data_root)
    return str(data_root / "attachments"), str(data_root / "artifacts")


def build_local_storage_adapters(
    settings: Settings,
    *,
    session_factory: Any | None,
    volume_paths: Any | None,
    sql_attachment_blobs: Any | None,
    sql_attachment_paths: Any | None,
    sql_artifact_blobs: Any | None,
) -> LocalStorageAdapters:
    """Build the local or SQL metadata adapters for a local runtime."""
    from fleet_rlm.artifacts.local_catalog import (
        LocalArtifactBlobGateway,
        LocalArtifactCatalog,
        LocalArtifactReaderCatalog,
    )
    from fleet_rlm.artifacts.reader import ArtifactReader
    from fleet_rlm.files.lifecycle import AttachmentLifecycleService
    from fleet_rlm.files.local_catalog import LocalAttachmentBlobGateway, LocalAttachmentCatalog
    from fleet_rlm.files.paths import LocalAttachmentPathPolicy
    from fleet_rlm.persistence.repositories import SqlAlchemyArtifactCatalog, SqlAlchemyAttachmentCatalog

    upload_root, artifact_root = host_roots(settings)
    if session_factory is None:
        attachment_lifecycle: Any = AttachmentLifecycleService(
            catalog=LocalAttachmentCatalog(upload_root),
            blobs=LocalAttachmentBlobGateway(Path(upload_root)),
            paths=LocalAttachmentPathPolicy(Path(upload_root)),
            max_bytes=settings.max_upload_bytes,
        )
        artifact_catalog = LocalArtifactCatalog(
            artifact_root,
            max_bytes=settings.max_artifact_bytes,
            volume_paths=volume_paths,
        )
        artifact_reader: Any = ArtifactReader(
            catalog=LocalArtifactReaderCatalog(artifact_catalog),
            blobs=LocalArtifactBlobGateway(artifact_catalog),
        )
        return LocalStorageAdapters(attachment_lifecycle, artifact_reader)

    if sql_attachment_blobs is None or sql_attachment_paths is None or sql_artifact_blobs is None:
        raise CompositionError("SQL local storage adapters require runtime-specific blob and path gateways")
    attachment_lifecycle = AttachmentLifecycleService(
        catalog=SqlAlchemyAttachmentCatalog(session_factory),
        blobs=sql_attachment_blobs,
        paths=sql_attachment_paths,
        max_bytes=settings.max_upload_bytes,
    )
    artifact_reader = ArtifactReader(
        catalog=SqlAlchemyArtifactCatalog(session_factory),
        blobs=sql_artifact_blobs,
    )
    return LocalStorageAdapters(attachment_lifecycle, artifact_reader)


def rlm_options(settings: Settings) -> RLMOptions:
    """Project Settings onto the exact native DSPy RLM options."""
    return RLMOptions(
        max_iterations=settings.rlm_max_iterations,
        max_llm_calls=settings.rlm_max_llm_calls,
        max_output_chars=settings.rlm_max_output_chars,
    )


def clear_composition_state(app: FastAPI) -> None:
    """Make every process-owned adapter unavailable after shutdown or rollback."""
    app.state.composition_ready = False
    for name in COMPOSITION_STATE_FIELDS:
        setattr(app.state, name, None)


def install_local_inventory(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None,
    attachment_lifecycle: Any,
    artifact_reader: Any,
    preparation: Any,
    rlm_factory: Any,
    workspace_volume_mirror: Any | None,
) -> LocalCompositionHandles:
    """Attach the shared in-memory/SQL inventory for one local runtime."""
    assert_dspy_version()
    from fleet_rlm.chat.turn_cleanup import TurnCleanupSupervisor
    from fleet_rlm.chat.turn_coordinator import TurnCoordinator
    from fleet_rlm.chat.turn_lifecycle import TurnLifecycleService
    from fleet_rlm.config import _CONFIG_PATH, _PROFILE_ENVIRONMENT
    from fleet_rlm.config_policy import ConfigPolicyService
    from fleet_rlm.persistence.repositories import (
        InMemorySessionCatalog,
        InMemoryTurnStateStore,
        SqlAlchemySessionCatalog,
        SqlAlchemyTurnStateStore,
    )
    from fleet_rlm.rlm.runner import RLMRunner

    if session_factory is None:
        turn_state = InMemoryTurnStateStore()
        session_catalog = InMemorySessionCatalog(turn_state)
    else:
        turn_state = SqlAlchemyTurnStateStore(
            session_factory,
            stale_after_seconds=settings.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
    lifecycle = TurnLifecycleService(
        turn_state,
        max_artifact_bytes=settings.max_artifact_bytes,
        heartbeat_seconds=settings.run_heartbeat_seconds,
        stale_after_seconds=settings.run_stale_after_seconds,
    )
    cleanup = TurnCleanupSupervisor(max_jobs=8)
    coordinator = TurnCoordinator(
        lifecycle=lifecycle,
        preparation=preparation,
        runner=RLMRunner(factory=rlm_factory),
        turn_timeout_seconds=settings.turn_timeout_seconds,
        cleanup=cleanup,
        claim_loss_fence=None,
        mlflow_tracing_enabled=settings.mlflow_tracing_enabled,
        mlflow_expose_trace_id=settings.mlflow_expose_trace_id,
    )
    handles = LocalCompositionHandles(
        turn_coordinator=coordinator,
        attachment_lifecycle=attachment_lifecycle,
        artifact_reader=artifact_reader,
        workspace_volume_mirror=workspace_volume_mirror,
        session_catalog=session_catalog,
        turn_lifecycle=lifecycle,
        turn_cleanup_supervisor=cleanup,
    )
    app.state.config_policy = ConfigPolicyService(
        _CONFIG_PATH,
        active_profile=os.environ.get(_PROFILE_ENVIRONMENT),
    )
    app.state.turn_coordinator = coordinator
    app.state.turn_preparation = preparation
    app.state.turn_lifecycle = lifecycle
    app.state.turn_cleanup_supervisor = cleanup
    app.state.turn_state_store = turn_state
    app.state.session_catalog = session_catalog
    app.state.attachment_lifecycle = attachment_lifecycle
    app.state.artifact_reader = artifact_reader
    app.state.workspace_volume_mirror = workspace_volume_mirror
    app.state.composition_ready = True
    return handles
