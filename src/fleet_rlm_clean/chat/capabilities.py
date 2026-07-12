"""Assemble host-mediated turn capabilities (skills + files) behind one seam.

Callers supply stores/registry; this module builds Skill/File hosts, SkillCards,
validated AttachmentRefs, and the tools tuple for dspy.RLM — without HTTP or Daytona.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fleet_rlm_clean.artifacts.store import LocalArtifactStore
from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.files.errors import AttachmentNotFoundError
from fleet_rlm_clean.files.models import AttachmentRef
from fleet_rlm_clean.files.tools import FileToolHost
from fleet_rlm_clean.files.uploads import LocalAttachmentStore
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.skills.authorize import SkillAuthorizer
from fleet_rlm_clean.skills.registry import InMemorySkillRegistry
from fleet_rlm_clean.skills.tools import SkillToolHost


class AttachmentValidationError(ValueError):
    """Public-safe: one or more attachment ids are missing or not owned."""

    def __init__(self, message: str = "invalid attachment reference") -> None:
        super().__init__(message)


def validate_attachment_ids(
    store: LocalAttachmentStore,
    attachment_ids: tuple[UUID, ...] | list[UUID],
    *,
    user_id: UUID,
    workspace_id: UUID,
) -> tuple[AttachmentRef, ...]:
    """Resolve attachment metadata for the principal; fail closed on any miss."""
    refs: list[AttachmentRef] = []
    for raw in attachment_ids:
        try:
            aid = raw if isinstance(raw, UUID) else UUID(str(raw))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AttachmentValidationError("invalid attachment reference") from exc
        try:
            refs.append(store.get(aid, user_id=user_id, workspace_id=workspace_id))
        except AttachmentNotFoundError as exc:
            raise AttachmentValidationError("invalid attachment reference") from exc
    return tuple(refs)


def assemble_turn_capabilities(
    context: RLMTurnContext,
    command: ChatTurnCommand,
    *,
    skill_registry: InMemorySkillRegistry | None = None,
    attachment_store: LocalAttachmentStore | None = None,
    artifact_store: LocalArtifactStore | None = None,
) -> RLMTurnContext:
    """Return a new context with tools, hosts, cards, and attachment metadata bound.

    Does not acquire Daytona resources. Attachment ids are revalidated when a store
    is provided. Skill bodies never appear on cards.
    """
    tools: list[Any] = list(context.tools or ())
    skill_host: SkillToolHost | None = getattr(context, "skill_tool_host", None)
    file_host: FileToolHost | None = getattr(context, "file_tool_host", None)
    skill_cards = tuple(context.skill_cards or ())
    attachments = tuple(context.attachments or ())

    budget = context.budget
    max_skill_loads = int(getattr(budget, "max_skill_loads", 8) or 8)

    if skill_registry is not None:
        authorizer = SkillAuthorizer(skill_registry)
        skill_cards = authorizer.list_cards(
            user_id=command.user_id,
            workspace_id=command.workspace_id,
        )
        skill_host = SkillToolHost(
            authorizer,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            max_skill_loads=max_skill_loads,
        )
        tools.extend(skill_host.as_tool_callables())

    if attachment_store is not None and command.attachment_ids:
        attachments = validate_attachment_ids(
            attachment_store,
            command.attachment_ids,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
        )
    elif attachment_store is not None:
        attachments = ()

    if attachment_store is not None and artifact_store is not None:
        file_host = FileToolHost(
            attachment_store=attachment_store,
            artifact_store=artifact_store,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            session_id=command.session_id,
            run_id=context.run_id,
        )
        tools.extend(file_host.as_tool_callables())

    # rebind_turn_context does not set tools/hosts/cards — construct explicitly
    return RLMTurnContext(
        run_id=context.run_id,
        session_id=context.session_id,
        user_id=context.user_id,
        workspace_id=context.workspace_id,
        request=context.request,
        models=context.models,
        budget=context.budget,
        lease=context.lease,
        history=context.history,
        session_summary=context.session_summary,
        skill_cards=skill_cards,
        attachments=attachments,
        artifacts=context.artifacts,
        tools=tuple(tools),
        skill_tool_host=skill_host,
        file_tool_host=file_host,
    )


class CapabilityContextBuilder:
    """Decorator builder: inner build → capability assembly.

    Satisfies TurnContextBuilder (``.build(command)``).
    """

    def __init__(
        self,
        inner: Any,
        *,
        skill_registry: InMemorySkillRegistry | None = None,
        attachment_store: LocalAttachmentStore | None = None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self._inner = inner
        self._skill_registry = skill_registry
        self._attachment_store = attachment_store
        self._artifact_store = artifact_store

    def build(self, command: ChatTurnCommand) -> RLMTurnContext:
        build = getattr(self._inner, "build", None)
        if callable(build):
            base = build(command)
        elif callable(self._inner):
            base = self._inner(command)
        else:
            msg = "inner context builder must be callable or provide build(command)"
            raise TypeError(msg)
        return assemble_turn_capabilities(
            base,
            command,
            skill_registry=self._skill_registry,
            attachment_store=self._attachment_store,
            artifact_store=self._artifact_store,
        )
