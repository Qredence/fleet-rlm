"""Deno/Pyodide WASM Run environment: real LLM calls, local WASM code execution."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol
from uuid import UUID

import dspy

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    RunEnvironment,
    RunEnvironmentProvider,
    TurnPreparationCancelled,
    TurnPreparationModule,
    TurnPreparationTimeout,
)
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.files.workspace_models import DENO_WORKSPACE_CAPABILITY
from fleet_rlm.rlm.context import PreparationNotice
from fleet_rlm.rlm.dspy_contract import RLMOptions
from fleet_rlm.rlm.events import AttachmentRead, SkillActivated, SkillLoaded
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.skills.authorize import SkillAuthorizer
from fleet_rlm.skills.capabilities import (
    CapabilityRegistry,
    CapabilityResolutionContext,
    CapabilityResolver,
    TurnCapabilityBlueprint,
)
from fleet_rlm.skills.registry import InMemorySkillRegistry


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


class DenoPreparedCapabilities:
    """Run-bound Skill/Attachment tools for Deno mode (no HTTP broker)."""

    def __init__(
        self,
        blueprint: TurnCapabilityBlueprint,
        *,
        files: Any,
        skills: Any,
        preparation_notices: tuple[PreparationNotice, ...] = (),
    ) -> None:
        self.blueprint = blueprint
        self._files = files
        self._skills = skills
        self.preparation_notices = preparation_notices

    def drain_public_details(self) -> tuple[AttachmentRead | SkillActivated | SkillLoaded, ...]:
        values: list[AttachmentRead | SkillActivated | SkillLoaded] = []
        for item in self._files.drain_public_events():
            values.append(
                AttachmentRead(
                    UUID(item["attachment_id"]),
                    str(item["filename"]),
                    int(item["byte_size"]),
                )
            )
        for item in self._skills.drain_public_events():
            values.append(_skill_event(item))
        return tuple(values)

    def drain_artifact_candidates(self) -> tuple[()]:
        """Deno does not promote Artifact Candidates; durable tools are excluded."""
        return ()

    async def aclose(self) -> None:
        return None


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
    """Prepares host-mediated tools for the Deno interpreter path.

    Uses the same CapabilityResolver as the Daytona path, but injects tool
    callables directly (no HTTP broker, no Daytona SDK).
    """

    def __init__(
        self,
        *,
        skill_registry: InMemorySkillRegistry,
        capability_registry: CapabilityRegistry | None = None,
        models: RLMModelBundle,
        options: RLMOptions,
        max_artifact_bytes: int,
    ) -> None:
        self._skill_registry = skill_registry
        self._capability_registry = capability_registry or CapabilityRegistry()
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
        from fleet_rlm.skills.tools import SkillToolHost

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
        file_tools = tuple(tool for tool in file_host.as_tools() if str(tool.name) != "create_artifact")
        history_host = SessionHistoryToolHost(turn.history)
        history_tools = history_host.as_tools()

        authorizer = SkillAuthorizer(self._skill_registry)
        selections = tuple(turn.input.skill_selections)
        try:
            if getattr(self._skill_registry, "unavailable", False):
                raise RuntimeError("skills unavailable")
            cards = authorizer.list_cards(
                user_id=turn.access.user_id,
                workspace_id=turn.access.workspace_id,
            )
            selected_records = (
                authorizer.authorize_explicit_many(
                    selections,
                    user_id=turn.access.user_id,
                    workspace_id=turn.access.workspace_id,
                )
                if selections
                else ()
            )
        except Exception as exc:
            from fleet_rlm.skills.authorize import InvalidSkillSelectionError

            if selections:
                if isinstance(exc, InvalidSkillSelectionError):
                    raise
                raise InvalidSkillSelectionError() from None
            blueprint = TurnCapabilityBlueprint(
                tools=(*file_tools, *history_tools),
                tool_event_views={
                    name: view
                    for name, view in {**file_host.event_views(), **history_host.event_views()}.items()
                    if name != "create_artifact"
                },
                workspace=DENO_WORKSPACE_CAPABILITY,
            )
            return DenoPreparedCapabilities(
                blueprint,
                files=file_host,
                skills=_EmptySkillHost(),
                preparation_notices=(PreparationNotice("skills_unavailable", "Skills are unavailable"),),
            )

        if await turn.cancellation_requested():
            raise TurnPreparationCancelled("Turn cancelled")
        if asyncio.get_running_loop().time() >= deadline:
            raise TurnPreparationTimeout("Turn preparation timed out")
        allowed_ids = frozenset(record.id for record in selected_records) if selections else None
        skill_host = SkillToolHost(
            authorizer,
            user_id=turn.access.user_id,
            workspace_id=turn.access.workspace_id,
            allowed_skill_ids=allowed_ids,
        )

        tools = (*file_tools, *history_tools, *skill_host.as_tools())
        tool_event_views = {
            name: view
            for name, view in {
                **file_host.event_views(),
                **history_host.event_views(),
                **skill_host.event_views(),
            }.items()
            if name != "create_artifact"
        }
        try:
            blueprint = await CapabilityResolver(self._capability_registry).resolve(
                CapabilityResolutionContext(
                    request=turn.input.text,
                    history=[{"role": item.role, "content": item.content} for item in turn.history.messages],
                    models=self._models,
                    options=self._options,
                    skill_cards=cards,
                    selected_skills=selected_records,
                    attachments=attachments.refs,
                    tools=tools,
                    tool_event_views=tool_event_views,
                    workspace=DENO_WORKSPACE_CAPABILITY,
                    deadline=deadline,
                )
            )
        except TimeoutError as exc:
            raise TurnPreparationTimeout("Turn preparation timed out") from exc
        except Exception:
            from fleet_rlm.skills.authorize import InvalidSkillSelectionError

            if selections:
                raise InvalidSkillSelectionError() from None
            raise
        for record in selected_records:
            skill_host.mark_preloaded(record)

        return DenoPreparedCapabilities(blueprint, files=file_host, skills=skill_host)


class _EmptySkillHost:
    def drain_public_events(self) -> list[dict[str, Any]]:
        return []


def _skill_event(item: dict[str, Any]) -> SkillActivated | SkillLoaded:
    if item.get("kind") == "skill.activated":
        return SkillActivated(
            str(item["skill_id"]),
            str(item["name"]),
            str(item["version"]),
            str(item["trust"]),
            tuple(str(value) for value in item.get("affordances", ())),
        )
    return SkillLoaded(str(item["skill_id"]), str(item["name"]), str(item["version"]))


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
        turn_timeout_seconds: int = 900,
        root_lm: dspy.LM,
        sub_lm: dspy.LM,
        skill_registry: InMemorySkillRegistry,
        capability_registry: CapabilityRegistry | None = None,
        max_artifact_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        selected_options = options or RLMOptions()
        models = RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm)
        self._module = TurnPreparationModule(
            models=models,
            options=selected_options,
            turn_timeout_seconds=turn_timeout_seconds,
            attachments=attachments,
            environments=DenoRunEnvironmentProvider(),
            capabilities=_DenoCapabilityPreparer(
                skill_registry=skill_registry,
                capability_registry=capability_registry,
                models=models,
                options=selected_options,
                max_artifact_bytes=max_artifact_bytes,
            ),
        )

    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn:
        return await self._module.prepare(turn)


__all__ = [
    "DenoRunSink",
    "DenoRunEnvironmentProvider",
    "DenoTurnPreparation",
]
