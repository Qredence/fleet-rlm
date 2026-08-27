"""Shared composition helpers and local inventory wiring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.chat.preparation import RunPreparation
from fleet_rlm.composition.inventory import RuntimeDatabaseLifecycle, RuntimeInventory
from fleet_rlm.config import Settings
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.volume_storage import VolumeTreeFs
from fleet_rlm.rlm._dspy_compat import assert_dspy_version
from fleet_rlm.rlm.program import RLMOptions
from fleet_rlm.rlm.recursion import RecursiveRLMOptions
from fleet_rlm.rlm.runtime import RLMFactoryLike
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry


class CompositionError(RuntimeError):
    """Raised when a runtime composition cannot be assembled."""


async def no_provider_recovery_fence(_session_id: UUID) -> None:
    """Declare that deterministic compositions have no provider state to fence."""


@dataclass(frozen=True, slots=True)
class LocalStorageAdapters:
    """Attachment and Artifact adapters shared by local runtime profiles."""

    attachment_lifecycle: AttachmentLifecycle
    artifact_reader: ArtifactReader


def host_roots(settings: Settings) -> tuple[str, str]:
    data_root = Path(settings.data_root)
    return str(data_root / "attachments"), str(data_root / "artifacts")


def build_local_storage_adapters(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
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
        attachment_lifecycle = AttachmentLifecycleService(
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
        artifact_reader = ArtifactReader(
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
        max_iters=settings.rlm_max_iters,
        max_llm_calls=settings.rlm_max_llm_calls,
        max_output_chars=settings.rlm_max_output_chars,
    )


def recursive_rlm_options(settings: Settings) -> RecursiveRLMOptions:
    """
    Create bounded options for recursive child RLM execution.

    Returns:
        RecursiveRLMOptions: Recursive execution settings derived from the application configuration.
    """
    return RecursiveRLMOptions(
        enabled=settings.rlm_recursion_enabled,
        max_calls=settings.rlm_recursion_max_calls,
        max_prompt_chars=settings.rlm_recursion_max_prompt_chars,
        child_max_iters=settings.rlm_recursion_child_max_iters,
        child_max_llm_calls=settings.rlm_recursion_child_max_llm_calls,
        child_max_output_chars=settings.rlm_recursion_child_max_output_chars,
        max_parallel_children=settings.rlm_recursion_max_parallel_children,
    )


def build_local_inventory(
    settings: Settings,
    *,
    database: RuntimeDatabaseLifecycle,
    attachment_lifecycle: AttachmentLifecycle,
    artifact_reader: ArtifactReader,
    preparation: RunPreparation,
    rlm_factory: RLMFactoryLike,
    workspace_volume_mirror: VolumeTreeFs | None,
    session_runtime_registry: SessionRLMRegistry | None = None,
) -> RuntimeInventory:
    """Build the shared in-memory/SQL inventory for one local runtime."""
    assert_dspy_version()
    from fleet_rlm.chat.run_cleanup import RunCleanupSupervisor
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.config import _CONFIG_PATH, active_profile
    from fleet_rlm.config_policy import ConfigPolicyService
    from fleet_rlm.persistence.repositories import (
        InMemoryRunStateStore,
        InMemorySessionCatalog,
        SqlAlchemyRunStateStore,
        SqlAlchemySessionCatalog,
    )
    from fleet_rlm.rlm.runtime import RLMRunner

    session_factory = database.session_factory
    if session_factory is None:
        run_state = InMemoryRunStateStore()
        session_catalog = InMemorySessionCatalog(run_state)
    else:
        run_state = SqlAlchemyRunStateStore(
            session_factory,
            stale_after_seconds=settings.run_stale_after_seconds,
        )
        session_catalog = SqlAlchemySessionCatalog(session_factory)
    cleanup = RunCleanupSupervisor(max_jobs=8)
    if session_runtime_registry is None:
        session_runtime_registry = SessionRLMRegistry()
    lifecycle = RunLifecycleService(
        run_state,
        max_artifact_bytes=settings.max_artifact_bytes,
        heartbeat_seconds=settings.run_heartbeat_seconds,
        stale_after_seconds=settings.run_stale_after_seconds,
        cleanup=cleanup,
    )
    runner = RLMRunner(factory=rlm_factory, runtime_registry=session_runtime_registry)
    coordinator = TurnRuntime(
        lifecycle=lifecycle,
        preparation=preparation,
        runner=runner,
        turn_timeout_seconds=settings.turn_timeout_seconds,
        cleanup=cleanup,
        claim_loss_fence=None,
        mlflow_tracing_enabled=settings.mlflow_tracing_enabled,
        mlflow_expose_trace_id=settings.mlflow_expose_trace_id,
    )
    return RuntimeInventory(
        turn_coordinator=coordinator,
        runner=runner,
        attachment_lifecycle=attachment_lifecycle,
        artifact_reader=artifact_reader,
        workspace_volume_mirror=workspace_volume_mirror,
        session_catalog=session_catalog,
        run_lifecycle=lifecycle,
        run_cleanup_supervisor=cleanup,
        run_preparation=preparation,
        run_state_store=run_state,
        session_runtime_registry=session_runtime_registry,
        config_policy=ConfigPolicyService(
            _CONFIG_PATH,
            active_profile=active_profile(settings),
        ),
        database=database,
    )
