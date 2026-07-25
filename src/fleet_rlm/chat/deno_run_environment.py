"""Deno/Pyodide WASM Run environment: real LLM calls, local WASM code execution."""

from __future__ import annotations

from typing import Any, Protocol

import dspy

from fleet_rlm.chat.capability_preparation import (
    PreparedHostCapabilities,
    prepare_host_capabilities,
)
from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    DefaultTurnPreparer,
    PreparedTurn,
    RunEnvironment,
    RunEnvironmentProvider,
)
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.files.workspace_models import DENO_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.context import RLMExecutionSpec
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.skills.catalog import SkillCatalog


class DenoRunSink:
    """In-memory bounded sink for attachments and artifacts in Deno mode."""

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


class DenoRunEnvironmentProvider(RunEnvironmentProvider):
    async def acquire(self, turn: ExecuteTurn, *, deadline: float) -> RunEnvironment:
        del turn, deadline
        sink = DenoRunSink()

        async def release() -> None:
            return None

        return RunEnvironment(None, sink, sink, release)


class DenoPreparedCapabilities(PreparedHostCapabilities):
    """Run-bound Skill/Attachment tools for Deno mode (no HTTP broker)."""

    def __init__(
        self,
        spec: RLMExecutionSpec,
        *,
        files: Any,
        skills: Any,
        preparation_notices: tuple[Any, ...] = (),
    ) -> None:
        super().__init__(
            spec,
            files=files,
            skills=skills,
            close_files=False,
            artifact_candidates=False,
            preparation_notices=preparation_notices,
        )


class _DenoSinkValues(Protocol):
    values: dict[str, bytes]


class _DenoVolumeFsAdapter:
    """Adapts a DenoRunSink to the synchronous VolumeBlobFs protocol used by FileToolHost.

    The Deno sink is async; FileToolHost calls these methods synchronously
    inside the prep phase, so we delegate to the underlying dict directly.
    """

    def __init__(self, sink: _DenoSinkValues) -> None:
        self._sink = sink

    def write_bytes(self, logical_path: str, data: bytes) -> None:
        self._sink.values[logical_path] = bytes(data)

    def read_bytes(self, logical_path: str) -> bytes:
        return self._sink.values[logical_path]

    def exists(self, logical_path: str) -> bool:
        return logical_path in self._sink.values

    def remove(self, logical_path: str) -> None:
        self._sink.values.pop(logical_path, None)


class _DenoCapabilityPreparer:
    """Prepare catalog-bound host tools for the Deno interpreter path."""

    def __init__(
        self,
        *,
        skill_catalog: SkillCatalog,
        models: RLMModelBundle,
        options: RLMOptions,
        max_artifact_bytes: int,
    ) -> None:
        self._skill_catalog = skill_catalog
        self._models = models
        self._options = options
        self._max_artifact_bytes = max_artifact_bytes

    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        *,
        deadline: float,
    ) -> DenoPreparedCapabilities:
        from fleet_rlm.files.tools import FileToolHost

        sink = environment.attachment_sink
        volume_fs: Any = (
            _DenoVolumeFsAdapter(sink)  # ty: ignore[invalid-argument-type] - DenoRunSink exposes `.values`
            if not hasattr(sink, "read_bytes")
            else sink
        )

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
        spec, skill_host, notices = await prepare_host_capabilities(
            turn=turn,
            skill_catalog=self._skill_catalog,
            files=file_host,
            base_tools=file_tools,
            base_event_views=file_event_views,
            workspace=DENO_WORKSPACE_CAPABILITY,
            deadline=deadline,
        )
        return DenoPreparedCapabilities(
            spec,
            files=file_host,
            skills=skill_host,
            preparation_notices=notices,
        )


class DenoTurnPreparation:
    """Build Deno turns through the same module as Daytona turns.

    Uses real dspy.LM models (Root + Sub) and the real Skill system.
    Attachments and artifacts are kept in-process (no durable storage).
    """

    def __init__(
        self,
        *,
        attachments: AttachmentLifecycle,
        options: RLMOptions | None = None,
        root_lm: dspy.LM,
        sub_lm: dspy.LM,
        skill_catalog: SkillCatalog,
        max_artifact_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        selected_options = options or RLMOptions()
        models = RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm)
        self._module = DefaultTurnPreparer(
            models=models,
            options=selected_options,
            attachments=attachments,
            environments=DenoRunEnvironmentProvider(),
            capabilities=_DenoCapabilityPreparer(
                skill_catalog=skill_catalog,
                models=models,
                options=selected_options,
                max_artifact_bytes=max_artifact_bytes,
            ),
        )

    async def prepare(self, turn: ExecuteTurn, *, deadline: float) -> PreparedTurn:
        return await self._module.prepare(turn, deadline=deadline)


__all__ = [
    "DenoRunSink",
    "DenoRunEnvironmentProvider",
    "DenoTurnPreparation",
]
