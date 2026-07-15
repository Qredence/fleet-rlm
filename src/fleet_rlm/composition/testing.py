"""Private deterministic composition used only by tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    RunEnvironment,
    RunEnvironmentProvider,
    TurnPreparationModule,
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
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint


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


class TestingPreparedCapabilities:
    blueprint = TurnCapabilityBlueprint()

    def drain_public_details(self) -> tuple[()]:
        return ()

    def drain_artifact_candidates(self) -> tuple[()]:
        return ()

    async def aclose(self) -> None:
        return None


class TestingCapabilityPreparer:
    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
    ) -> TestingPreparedCapabilities:
        del turn, environment, attachments
        return TestingPreparedCapabilities()


class _TestingRLM:
    async def acall(self, **kwargs: Any) -> SimpleNamespace:
        request = str(kwargs.get("request") or "").strip()
        return SimpleNamespace(answer=request)


class TestingRLMFactory:
    """Deterministic RLM substitute that never calls a provider."""

    def create(self, **kwargs: Any) -> _TestingRLM:
        del kwargs
        return _TestingRLM()


class DeterministicTurnPreparation:
    def __init__(
        self,
        *,
        attachments: AttachmentLifecycle,
        options: RLMOptions | None = None,
        turn_timeout_seconds: int = 900,
    ) -> None:
        self._module = TurnPreparationModule(
            models=RLMModelBundle(TestingLM("testing/root"), TestingLM("testing/sub")),
            options=options or RLMOptions(),
            turn_timeout_seconds=turn_timeout_seconds,
            attachments=attachments,
            environments=TestingRunEnvironmentProvider(),
            capabilities=TestingCapabilityPreparer(),
        )

    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn:
        return await self._module.prepare(turn)


def install_testing_composition(
    app: FastAPI,
    settings: Settings,
    *,
    session_factory: Any | None = None,
) -> LocalCompositionHandles:
    """Install credential-free deterministic adapters for a test lifespan."""
    from fleet_rlm.artifacts.daytona_catalog import DaytonaArtifactBlobGateway
    from fleet_rlm.daytona.paths import volume_paths_from_settings
    from fleet_rlm.daytona.volume_fs import HostVolumeMirror
    from fleet_rlm.daytona.workspace_volume import OfflineHostVolumeGateway
    from fleet_rlm.files.local_catalog import (
        WorkspaceAttachmentBlobGateway,
    )
    from fleet_rlm.files.paths import DaytonaAttachmentPathPolicy

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
        sql_attachment_paths=DaytonaAttachmentPathPolicy(mirror.volume_paths),
        sql_artifact_blobs=DaytonaArtifactBlobGateway(volume_gateway),
    )
    return install_local_inventory(
        app,
        settings,
        session_factory=session_factory,
        attachment_lifecycle=storage.attachment_lifecycle,
        artifact_reader=storage.artifact_reader,
        preparation=DeterministicTurnPreparation(
            attachments=storage.attachment_lifecycle,
            options=rlm_options(settings),
            turn_timeout_seconds=settings.turn_timeout_seconds,
        ),
        rlm_factory=TestingRLMFactory(),
        workspace_volume_mirror=mirror,
    )


def create_testing_app(*, settings: Settings | None = None) -> FastAPI:
    """Create an app whose lifespan explicitly installs private test adapters."""
    from fleet_rlm.app import create_app

    if settings is None:
        settings_factory: Any = Settings
        resolved = settings_factory(_env_file=None, run_environment="daytona")
    else:
        resolved = settings
    return create_app(settings=resolved, _composition_installer=install_testing_composition)
