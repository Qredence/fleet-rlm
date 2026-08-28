"""Private deterministic composition used only by tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fleet_rlm.workspace.url import UrlFetchResult

import dspy
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fleet_rlm.artifacts.reader import ArtifactReader
from fleet_rlm.attachments.lifecycle import AttachmentLifecycle
from fleet_rlm.attachments.models import PreparedAttachments
from fleet_rlm.chat.capability_preparation import PreparedHostCapabilities, prepare_host_capabilities
from fleet_rlm.chat.preparation import (
    DefaultRunPreparer,
    PreparedRun,
    RunEnvironment,
    RunEnvironmentProvider,
    RunPreparation,
)
from fleet_rlm.chat.run_lifecycle import ClaimedRun
from fleet_rlm.composition.inventory import (
    CompositionError,
    RuntimeDatabaseLifecycle,
    RuntimeInventory,
    install_runtime_inventory,
)
from fleet_rlm.config.settings import Settings
from fleet_rlm.rlm._dspy_compat import assert_dspy_version
from fleet_rlm.rlm.program import FleetRLMSignature, RLMModelBundle, RLMOptions, rlm_options
from fleet_rlm.rlm.recursion import RecursiveRLMOptions
from fleet_rlm.rlm.runtime import RLMFactoryLike
from fleet_rlm.rlm.session_runtime import SessionRLMRegistry
from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog
from fleet_rlm.workspace.models import UNAVAILABLE_WORKSPACE_CAPABILITY


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
    from fleet_rlm.attachments.lifecycle import AttachmentLifecycleService
    from fleet_rlm.attachments.local_catalog import LocalAttachmentBlobGateway, LocalAttachmentCatalog
    from fleet_rlm.attachments.paths import LocalAttachmentPathPolicy
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


def build_local_inventory(
    settings: Settings,
    *,
    database: RuntimeDatabaseLifecycle,
    attachment_lifecycle: AttachmentLifecycle,
    artifact_reader: ArtifactReader,
    preparation: RunPreparation,
    rlm_factory: RLMFactoryLike,
    session_runtime_registry: SessionRLMRegistry | None = None,
) -> RuntimeInventory:
    """Build the shared in-memory/SQL inventory for one local runtime."""
    assert_dspy_version()
    from fleet_rlm.chat.run_lifecycle import RunLifecycleService
    from fleet_rlm.chat.turn_runtime import TurnRuntime
    from fleet_rlm.config.policy import ConfigPolicyService
    from fleet_rlm.persistence.repositories import (
        InMemoryRunStateStore,
        InMemorySessionCatalog,
        SqlAlchemyRunStateStore,
        SqlAlchemySessionCatalog,
    )
    from fleet_rlm.rlm.runtime import RLMRunner
    from fleet_rlm.runtime.cleanup import RunCleanupSupervisor

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
        session_catalog=session_catalog,
        run_lifecycle=lifecycle,
        run_cleanup_supervisor=cleanup,
        run_preparation=preparation,
        run_state_store=run_state,
        session_runtime_registry=session_runtime_registry,
        config_policy=ConfigPolicyService.from_settings(settings),
        database=database,
    )


class TestingLM:
    def __init__(self, name: str) -> None:
        self.model = name


class TestingInterpreter:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    @property
    def tools(self) -> dict[str, Callable[..., Any]]:
        return self._tools

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> str:
        del code, variables
        return ""

    def shutdown(self) -> None:
        return None

    def drain_context_accesses(self) -> tuple[str, ...]:
        return ()


class TestingRunSink:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        value = self.values[location]
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        self.values[location] = bytes(data)

    async def remove(self, location: str) -> None:
        self.values.pop(location, None)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


class _TestingVolumeFsAdapter:
    """Synchronous volume adapter over the deterministic in-memory test sink."""

    def __init__(self, sink: TestingRunSink) -> None:
        self._sink = sink

    def write_bytes(self, logical_path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("volume value exceeds its byte bound")
        self._sink.values[logical_path] = bytes(data)

    def read_bytes(
        self,
        logical_path: str,
        *,
        max_bytes: int | None = None,
        use_cache: bool = True,
    ) -> bytes:
        del use_cache
        value = self._sink.values[logical_path]
        if max_bytes is not None and len(value) > max_bytes:
            raise ValueError("volume value exceeds its byte bound")
        return value

    def exists(self, logical_path: str) -> bool:
        return logical_path in self._sink.values

    def remove(self, logical_path: str) -> None:
        self._sink.values.pop(logical_path, None)


class TestingRunEnvironmentProvider(RunEnvironmentProvider):
    async def acquire(self, run: ClaimedRun, *, deadline: float) -> RunEnvironment:
        del run, deadline
        sink = TestingRunSink()

        async def release() -> None:
            return None

        return RunEnvironment(TestingInterpreter(), sink, sink, release)


class _TestingCacheOnlyUrlFetcher:
    """Deterministic cache-only fetcher: private tests never open the network."""

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        from fleet_rlm.workspace.url import UrlToolError

        del url, max_bytes
        raise UrlToolError(
            "unavailable",
            "URL fetching is disabled in the deterministic test composition",
        )


class TestingCapabilityPreparer:
    def __init__(
        self,
        *,
        skill_catalog: SkillCatalog,
        models: RLMModelBundle,
        options: RLMOptions,
        max_artifact_bytes: int = 10_000_000,
        max_url_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        """Initialize a testing capability preparer with configured source limits."""
        from fleet_rlm.workspace.url import InMemoryUrlSourceStore

        del models, options
        self._skill_catalog = skill_catalog
        self._max_artifact_bytes = max_artifact_bytes
        self._max_url_bytes = max(1, int(max_url_bytes))
        self._url_store = InMemoryUrlSourceStore()

    async def prepare(
        self,
        run: ClaimedRun,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedHostCapabilities:
        """Prepare host capabilities for one turn within the execution deadline."""
        from fleet_rlm.attachments.tools import AttachmentToolHost
        from fleet_rlm.workspace.url import UrlToolHost

        sink = environment.attachment_sink
        if not isinstance(sink, TestingRunSink):
            raise TypeError("testing capabilities require the testing run sink")
        volume_fs = _TestingVolumeFsAdapter(sink)
        attachment_host = AttachmentToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
        )
        attachment_tools = attachment_host.as_tools()
        attachment_event_views = dict(attachment_host.event_views())
        url_host = UrlToolHost(
            session_id=run.session_id,
            store=self._url_store,
            max_bytes=self._max_url_bytes,
            fetcher=_TestingCacheOnlyUrlFetcher(),
        )
        url_tools = url_host.as_tools()
        url_event_views = url_host.event_views()
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=run,
            skill_catalog=self._skill_catalog,
            base_tools=(*attachment_tools, *url_tools),
            base_event_views={**attachment_event_views, **url_event_views},
            workspace=UNAVAILABLE_WORKSPACE_CAPABILITY,
            deadline=deadline,
        )
        return PreparedHostCapabilities(
            spec,
            files=attachment_host,
            skills=skill_host,
            close_files=False,
            artifact_candidates=False,
            preparation_notices=notices,
        )


class _TestingRLM:
    def __init__(self, signature: type[dspy.Signature]) -> None:
        self._signature = signature

    async def acall(self, **kwargs: Any) -> SimpleNamespace:
        request = str(kwargs.get("request") or "").strip()
        values: dict[str, Any] = {"answer": request}
        for name in self._signature.output_fields:
            if name == "answer":
                continue
            if name in {"findings", "anomalies", "metrics"}:
                values[name] = []
        return SimpleNamespace(**values, trajectory=[])


class TestingRLMFactory:
    """Deterministic RLM substitute that never calls a provider."""

    def create(self, **kwargs: Any) -> _TestingRLM:
        signature = kwargs.get("signature", FleetRLMSignature)
        return _TestingRLM(signature)


class DeterministicTurnPreparation:
    def __init__(
        self,
        *,
        attachments: AttachmentLifecycle,
        skill_catalog: SkillCatalog | None = None,
        options: RLMOptions | None = None,
        max_artifact_bytes: int = 10_000_000,
        max_url_bytes: int = 10 * 1024 * 1024,
        session_runtime_registry: SessionRLMRegistry | None = None,
    ) -> None:
        resolved_options = options or RLMOptions()
        models = RLMModelBundle(TestingLM("testing/root"), TestingLM("testing/sub"))
        self._module = DefaultRunPreparer(
            models=models,
            options=resolved_options,
            recursive_options=RecursiveRLMOptions(),
            attachments=attachments,
            environments=TestingRunEnvironmentProvider(),
            capabilities=TestingCapabilityPreparer(
                skill_catalog=skill_catalog or build_bundled_skill_catalog(),
                models=models,
                options=resolved_options,
                max_artifact_bytes=max_artifact_bytes,
                max_url_bytes=max_url_bytes,
            ),
            session_runtime_registry=session_runtime_registry,
        )

    async def prepare(self, run: ClaimedRun, *, deadline: float) -> PreparedRun:
        return await self._module.prepare(run, deadline=deadline)


def install_testing_composition(
    app: FastAPI,
    settings: Settings,
    *,
    database: RuntimeDatabaseLifecycle | None = None,
) -> RuntimeInventory:
    """Install credential-free deterministic adapters for a test lifespan."""
    from fleet_rlm.attachments.paths import WorkspaceAttachmentPathPolicy
    from fleet_rlm.workspace.paths import volume_paths_from_settings
    from fleet_rlm.workspace.storage import HostVolumeMirror, OfflineHostVolumeGateway
    from fleet_rlm.workspace.workspace import (
        HostWorkspaceAccessGateway,
        WorkspaceFileService,
    )

    upload_root, _artifact_root = host_roots(settings)
    database = database or RuntimeDatabaseLifecycle()
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(settings),
    )
    volume_gateway = OfflineHostVolumeGateway(mirror)
    session_runtime_registry = SessionRLMRegistry()
    storage = build_local_storage_adapters(
        settings,
        session_factory=database.session_factory,
        volume_paths=mirror.volume_paths,
        sql_attachment_blobs=volume_gateway,
        sql_attachment_paths=WorkspaceAttachmentPathPolicy(mirror.volume_paths),
        sql_artifact_blobs=volume_gateway,
    )
    local_inventory = build_local_inventory(
        settings,
        database=database,
        attachment_lifecycle=storage.attachment_lifecycle,
        artifact_reader=storage.artifact_reader,
        preparation=DeterministicTurnPreparation(
            attachments=storage.attachment_lifecycle,
            skill_catalog=app.state.skill_catalog,
            options=rlm_options(settings),
            max_artifact_bytes=settings.max_artifact_bytes,
            max_url_bytes=settings.max_url_bytes,
            session_runtime_registry=session_runtime_registry,
        ),
        rlm_factory=TestingRLMFactory(),
        session_runtime_registry=session_runtime_registry,
    )
    # Overlay only the host volume adapters; keep the shared local inventory
    # members so new RuntimeInventory fields cannot silently drop here.
    inventory = replace(
        local_inventory,
        workspace_volume_gateway=volume_gateway,
        workspace_file_service=WorkspaceFileService(
            cast(
                Any,
                HostWorkspaceAccessGateway(
                    Path(settings.data_root) / "workspace-files",
                    max_file_bytes=settings.max_upload_bytes,
                ),
            )
        ),
    )
    return install_runtime_inventory(app, inventory)


def create_testing_app(*, settings: Settings | None = None) -> FastAPI:
    """Create an app whose lifespan explicitly installs private test adapters."""
    from fleet_rlm.app import create_app

    if settings is None:
        settings_factory: Any = Settings
        resolved = settings_factory(run_environment="daytona")
    else:
        resolved = settings
    return create_app(settings=resolved, _composition_installer=install_testing_composition)
