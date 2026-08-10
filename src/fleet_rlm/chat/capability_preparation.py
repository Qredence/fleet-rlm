"""Shared host-owned capability preparation for Daytona and private Turns."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import dspy

from fleet_rlm.chat.run_lifecycle import ClaimedRun
from fleet_rlm.chat.run_preparation import RunPreparationCancelledError, RunPreparationTimeoutError
from fleet_rlm.files.workspace_models import SessionWorkspaceFS, WorkspaceCapabilityMetadata
from fleet_rlm.rlm.context import PreparationNotice, RLMExecutionSpec
from fleet_rlm.rlm.events import AttachmentRead, SkillActivated, SkillLoaded
from fleet_rlm.rlm.tool_observer import ToolEventView
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.skills.resolver import resolve_selected_skills, resolved_schema, resolved_signature
from fleet_rlm.skills.tools import SkillToolHost


class EmptySkillHost:
    """No-op ledger used only when the bundled host catalog is unavailable."""

    def drain_public_events(self) -> list[dict[str, Any]]:
        return []


def skill_event(item: Mapping[str, Any]) -> SkillActivated | SkillLoaded:
    if item.get("kind") == "skill.activated":
        return SkillActivated(
            str(item["skill_id"]),
            str(item["name"]),
            str(item["version"]),
            str(item["trust"]),
            tuple(str(value) for value in item.get("affordances", ())),
        )
    return SkillLoaded(str(item["skill_id"]), str(item["name"]), str(item["version"]))


class PreparedHostCapabilities:
    """Runtime-neutral execution spec plus host activation/loading ledgers."""

    def __init__(
        self,
        spec: RLMExecutionSpec,
        *,
        files: Any,
        skills: Any,
        close_files: bool,
        artifact_candidates: bool,
        preparation_notices: tuple[PreparationNotice, ...] = (),
        memory_candidates: Any = None,
    ) -> None:
        self.spec = spec
        self._files = files
        self._skills = skills
        self._close_files = close_files
        self._artifact_candidates = artifact_candidates
        self._memory_candidates = memory_candidates
        self.preparation_notices = preparation_notices

    def drain_public_details(self) -> tuple[AttachmentRead | SkillActivated | SkillLoaded, ...]:
        values: list[AttachmentRead | SkillActivated | SkillLoaded] = []
        for item in self._files.drain_public_events():
            # Only canonical attachment-read payloads project here. Artifact
            # candidate notices (``artifact.workspace_publish``) carry ``path``
            # instead of ``attachment_id`` and reach the stream through the
            # promotion path in ``RunLifecycle.finish``.
            if item.get("event_kind", "attachment.read") != "attachment.read":
                continue
            values.append(
                AttachmentRead(
                    UUID(str(item["attachment_id"])),
                    str(item["filename"]),
                    int(item["byte_size"]),
                )
            )
        values.extend(skill_event(item) for item in self._skills.drain_public_events())
        return tuple(values)

    def drain_artifact_candidates(self) -> Any:
        if not self._artifact_candidates:
            return ()
        return self._files.drain_artifact_candidates()

    def drain_memory_candidates(self) -> Any:
        """Drain Run-scoped memory proposals; empty when the policy did not expose them."""
        if self._memory_candidates is None:
            return ()
        return self._memory_candidates.drain()

    def promote_memory_candidates(self, candidates: tuple[Any, ...]) -> Any:
        """Default no-op result for private compositions without a memory store."""
        from fleet_rlm.files.memory_candidates import MemoryCandidatePromotionResult

        return MemoryCandidatePromotionResult(proposed_count=len(candidates))

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        recorder = getattr(self._files, "record_attachment_accesses", None)
        if callable(recorder):
            recorder(attachment_ids)

    async def aclose(self) -> None:
        if self._close_files:
            await self._files.aclose()


async def prepare_host_capabilities(
    *,
    turn: ClaimedRun,
    skill_catalog: SkillCatalog,
    base_tools: Sequence[dspy.Tool],
    base_event_views: Mapping[str, ToolEventView],
    workspace: WorkspaceCapabilityMetadata,
    workspace_fs: SessionWorkspaceFS | None = None,
    deadline: float,
) -> tuple[RLMExecutionSpec, SkillToolHost | EmptySkillHost, tuple[PreparationNotice, ...]]:
    """Resolve history and exact Skills identically for every Run environment."""
    history_host = SessionHistoryToolHost(turn.history)
    history_tools = history_host.as_tools()
    event_views = {**base_event_views, **history_host.event_views()}
    selections = tuple(turn.input.skill_selections)

    if getattr(skill_catalog, "unavailable", False):
        from fleet_rlm.skills.errors import InvalidSkillSelectionError

        if selections:
            raise InvalidSkillSelectionError() from None
        return (
            RLMExecutionSpec(
                skill_cards=(),
                tools=(*base_tools, *history_tools),
                tool_event_views=event_views,
                workspace=workspace,
            ),
            EmptySkillHost(),
            (PreparationNotice("skills_unavailable", "Skills are unavailable"),),
        )

    resolved = resolve_selected_skills(skill_catalog, selections)
    if await turn.cancellation_requested():
        raise RunPreparationCancelledError("Turn cancelled")
    if asyncio.get_running_loop().time() >= deadline:
        raise RunPreparationTimeoutError("Turn preparation timed out")

    skill_host = SkillToolHost(
        skill_catalog,
        allowed_skill_ids=(frozenset(skill.card.id for skill in resolved.selected) if selections else None),
        workspace=workspace_fs,
    )
    schema_id, schema_version = resolved_schema(resolved)
    spec = RLMExecutionSpec(
        skill_cards=resolved.cards,
        signature=resolved_signature(resolved),
        skill_instructions=resolved.instructions,
        output_schema_id=schema_id,
        output_schema_version=schema_version,
        tools=(*base_tools, *history_tools, *skill_host.as_tools()),
        tool_event_views={**event_views, **skill_host.event_views()},
        workspace=workspace,
    )
    for skill in resolved.selected:
        skill_host.mark_preloaded(skill)
    return spec, skill_host, ()


__all__ = [
    "EmptySkillHost",
    "PreparedHostCapabilities",
    "prepare_host_capabilities",
    "skill_event",
]
