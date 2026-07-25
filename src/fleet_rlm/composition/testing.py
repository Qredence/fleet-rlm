"""Private deterministic composition used only by tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
from fastapi import FastAPI

from fleet_rlm.chat.deno_run_environment import DenoPreparedCapabilities, _DenoCapabilityPreparer
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
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.signature import FleetRLMSignature
from fleet_rlm.skills.catalog import SkillCatalog, build_bundled_skill_catalog


class TestingLM:
    def __init__(self, name: str) -> None:
        self.model = name


class TestingInterpreter:
    def execute(self, code: str) -> str:
        del code
        return ""


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


class TestingRunEnvironmentProvider(RunEnvironmentProvider):
    async def acquire(self, turn: ExecuteTurn, *, deadline: float) -> RunEnvironment:
        del turn, deadline
        sink = TestingRunSink()

        async def release() -> None:
            return None

        return RunEnvironment(TestingInterpreter(), sink, sink, release)


class TestingCapabilityPreparer:
    def __init__(
        self, *, skill_catalog: SkillCatalog, models: RLMModelBundle, options: RLMOptions, max_artifact_bytes: int
    ) -> None:
        self._delegate = _DenoCapabilityPreparer(
            skill_catalog=skill_catalog,
            models=models,
            options=options,
            max_artifact_bytes=max_artifact_bytes,
        )

    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> DenoPreparedCapabilities:
        return await self._delegate.prepare(turn, environment, attachments, deadline=deadline)


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
    ) -> None:
        resolved_options = options or RLMOptions()
        models = RLMModelBundle(TestingLM("testing/root"), TestingLM("testing/sub"))
        self._module = DefaultTurnPreparer(
            models=models,
            options=resolved_options,
            attachments=attachments,
            environments=TestingRunEnvironmentProvider(),
            capabilities=TestingCapabilityPreparer(
                skill_catalog=skill_catalog or build_bundled_skill_catalog(),
                models=models,
                options=resolved_options,
                max_artifact_bytes=max_artifact_bytes,
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

    upload_root, artifact_root = host_roots(settings)
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
