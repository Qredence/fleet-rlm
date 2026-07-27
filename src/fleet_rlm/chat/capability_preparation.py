"""Shared host-owned capability preparation for Deno and Daytona Turns."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import dspy

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import TurnPreparationCancelledError, TurnPreparationTimeoutError
from fleet_rlm.files.workspace_models import WorkspaceCapabilityMetadata
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
    ) -> None:
        self.spec = spec
        self._files = files
        self._skills = skills
        self._close_files = close_files
        self._artifact_candidates = artifact_candidates
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
        values.extend(skill_event(item) for item in self._skills.drain_public_events())
        return tuple(values)

    def drain_artifact_candidates(self) -> Any:
        if not self._artifact_candidates:
            return ()
        return self._files.drain_artifact_candidates()

    async def aclose(self) -> None:
        if self._close_files:
            await self._files.aclose()


async def prepare_host_capabilities(
    *,
    turn: ExecuteTurn,
    skill_catalog: SkillCatalog,
    files: Any,
    base_tools: Sequence[dspy.Tool],
    base_event_views: Mapping[str, ToolEventView],
    workspace: WorkspaceCapabilityMetadata,
    deadline: float,
) -> tuple[RLMExecutionSpec, SkillToolHost | EmptySkillHost, tuple[PreparationNotice, ...]]:
    del files
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
        raise TurnPreparationCancelledError("Turn cancelled")
    if asyncio.get_running_loop().time() >= deadline:
        raise TurnPreparationTimeoutError("Turn preparation timed out")

    skill_host = SkillToolHost(
        skill_catalog,
        allowed_skill_ids=(frozenset(skill.card.id for skill in resolved.selected) if selections else None),
    )
    schema_id, schema_version = resolved_schema(resolved)
    spec = RLMExecutionSpec(
        skill_cards=resolved.cards,
        signature=resolved_signature(resolved),
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
