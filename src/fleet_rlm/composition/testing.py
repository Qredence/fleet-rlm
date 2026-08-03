"""Private deterministic composition used only by tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fleet_rlm.files.url_tool import UrlFetchResult

import dspy
from fastapi import FastAPI

from fleet_rlm.chat.capability_preparation import PreparedHostCapabilities, prepare_host_capabilities
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    DefaultTurnPreparer,
    PreparedTurn,
    RunEnvironment,
    RunEnvironmentProvider,
)
from fleet_rlm.composition.common import (
    LocalCompositionHandles,
    build_local_storage_adapters,
    host_roots,
    install_local_inventory,
    rlm_options,
)
from fleet_rlm.config import Settings
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.files.workspace_models import UNAVAILABLE_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMOptions
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog


class TestingLM:
    def __init__(self, name: str) -> None:
        self.model = name


class TestingInterpreter:
    def execute(self, code: str) -> str:
        del code
        return ""

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

    async def read_private(self, logical_path: str) -> bytes:
        return await self.read(logical_path, max_bytes=2**31 - 1)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


class _TestingVolumeFsAdapter:
    """Synchronous volume adapter over the deterministic in-memory test sink."""

    def __init__(self, sink: TestingRunSink) -> None:
        self._sink = sink

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        self._sink.values[logical_path] = bytes(data)

    def read_bytes(self, logical_path: str) -> bytes:
        return self._sink.values[logical_path]

    def exists(self, logical_path: str) -> bool:
        return logical_path in self._sink.values

    def remove(self, logical_path: str) -> None:
        self._sink.values.pop(logical_path, None)


class TestingRunEnvironmentProvider(RunEnvironmentProvider):
    async def acquire(self, turn: ExecuteTurn, *, deadline: float) -> RunEnvironment:
        del turn, deadline
        sink = TestingRunSink()

        async def release() -> None:
            return None

        return RunEnvironment(TestingInterpreter(), sink, sink, release)


class _TestingCacheOnlyUrlFetcher:
    """Deterministic cache-only fetcher: private tests never open the network."""

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        from fleet_rlm.files.url_tool import UrlToolError

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
        """Initialize a testing capability preparer with configured source limits.

        Parameters:
            skill_catalog (SkillCatalog): Catalog of available skills.
            models (RLMModelBundle): Models used for capability preparation.
            options (RLMOptions): Runtime options for capability preparation.
            max_artifact_bytes (int): Maximum permitted artifact size in bytes.
            max_url_bytes (int): Maximum permitted URL source size in bytes.
        """
        from fleet_rlm.files.url_tool import InMemoryUrlSourceStore

        del models, options
        self._skill_catalog = skill_catalog
        self._max_artifact_bytes = max_artifact_bytes
        self._max_url_bytes = max(1, int(max_url_bytes))
        self._url_store = InMemoryUrlSourceStore()

    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> PreparedHostCapabilities:
        """Prepare capabilities for a turn within the specified execution environment and deadline.

        Parameters:
            turn (ExecuteTurn): The turn whose capabilities are being prepared.
            environment (RunEnvironment): The environment in which the turn will execute.
            attachments (PreparedAttachments): Attachments available to the turn.
            deadline (float): The time limit for preparation.

        Returns:
            PreparedHostCapabilities: The prepared capabilities.
        """
        from fleet_rlm.files.tools import FileToolHost
        from fleet_rlm.files.url_tool import UrlToolHost

        sink = environment.attachment_sink
        if not isinstance(sink, TestingRunSink):
            raise TypeError("testing capabilities require the testing run sink")
        volume_fs = _TestingVolumeFsAdapter(sink)
        file_host = FileToolHost(
            attachments=attachments.refs,
            staged_attachments=attachments.staged,
            volume_fs=volume_fs,
            user_id=turn.access.user_id,
            workspace_id=turn.access.workspace_id,
            session_id=turn.session_id,
            run_id=turn.run_id,
            max_artifact_bytes=self._max_artifact_bytes,
            volume_paths=None,
        )
        file_tools = tuple(
            tool
            for tool in file_host.as_tools()
            if str(tool.name) not in {"create_artifact", "publish_workspace_artifact"}
        )
        file_event_views = {
            name: view
            for name, view in file_host.event_views().items()
            if name not in {"create_artifact", "publish_workspace_artifact"}
        }
        url_host = UrlToolHost(
            session_id=turn.session_id,
            store=self._url_store,
            max_bytes=self._max_url_bytes,
            fetcher=_TestingCacheOnlyUrlFetcher(),
        )
        url_tools = url_host.as_tools()
        url_event_views = url_host.event_views()
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=turn,
            skill_catalog=self._skill_catalog,
            files=file_host,
            base_tools=(*file_tools, *url_tools),
            base_event_views={**file_event_views, **url_event_views},
            workspace=UNAVAILABLE_WORKSPACE_CAPABILITY,
            deadline=deadline,
        )
        return PreparedHostCapabilities(
            spec,
            files=file_host,
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
    ) -> None:
        resolved_options = options or RLMOptions()
        models = RLMModelBundle(TestingLM("testing/root"), TestingLM("testing/sub"))
        self._module = DefaultTurnPreparer(
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
        )

    async def prepare(self, turn: ExecuteTurn, *, deadline: float) -> PreparedTurn:
        return await self._module.prepare(turn, deadline=deadline)


def install_testing_composition(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> LocalCompositionHandles:
    """Install credential-free deterministic adapters for a test lifespan."""
    from fleet_rlm.artifacts.workspace_storage import WorkspaceArtifactBlobGateway
    from fleet_rlm.files.host_volume import HostVolumeMirror, OfflineHostVolumeGateway
    from fleet_rlm.files.local_catalog import (
        WorkspaceAttachmentBlobGateway,
    )
    from fleet_rlm.files.paths import WorkspaceAttachmentPathPolicy
    from fleet_rlm.files.volume_paths import volume_paths_from_settings
    from fleet_rlm.files.workspace_access import (
        HostWorkspaceAccessGateway,
        WorkspaceFileService,
    )

    upload_root, _artifact_root = host_roots(settings)
    mirror = HostVolumeMirror(
        Path(upload_root) / "_workspace_volume",
        volume_paths=volume_paths_from_settings(settings),
    )
    volume_gateway = OfflineHostVolumeGateway(mirror)
    storage = build_local_storage_adapters(
        settings,
        session_factory=session_factory,
        volume_paths=mirror.volume_paths,
        sql_attachment_blobs=WorkspaceAttachmentBlobGateway(volume_gateway),
        sql_attachment_paths=WorkspaceAttachmentPathPolicy(mirror.volume_paths),
        sql_artifact_blobs=WorkspaceArtifactBlobGateway(volume_gateway),
    )
    handles = install_local_inventory(
        app,
        settings,
        session_factory=session_factory,
        attachment_lifecycle=storage.attachment_lifecycle,
        artifact_reader=storage.artifact_reader,
        preparation=DeterministicTurnPreparation(
            attachments=storage.attachment_lifecycle,
            skill_catalog=app.state.skill_catalog,
            options=rlm_options(settings),
            max_artifact_bytes=settings.max_artifact_bytes,
            max_url_bytes=settings.max_url_bytes,
        ),
        rlm_factory=TestingRLMFactory(),
        workspace_volume_mirror=mirror,
    )
    app.state.workspace_file_service = WorkspaceFileService(
        HostWorkspaceAccessGateway(
            Path(settings.data_root) / "workspace-files",
            max_file_bytes=settings.max_upload_bytes,
        )
    )
    app.state.workspace_volume_gateway = volume_gateway
    return handles


def create_testing_app(*, settings: Settings | None = None) -> FastAPI:
    """Create an app whose lifespan explicitly installs private test adapters."""
    from fleet_rlm.app import create_app

    if settings is None:
        settings_factory: Any = Settings
        resolved = settings_factory(_env_file=None, run_environment="daytona")
    else:
        resolved = settings
    return create_app(settings=resolved, _composition_installer=install_testing_composition)
