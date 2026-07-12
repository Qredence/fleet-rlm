"""Assemble host-mediated turn capabilities (skills + files) behind one seam.

Callers supply stores/registry; this module builds Skill/File hosts, SkillCards,
validated AttachmentRefs, and the tools tuple for dspy.RLM — without HTTP or Daytona.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from uuid import UUID

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.context_builder import rebind_turn_context
from fleet_rlm_clean.daytona.paths import VolumePaths, as_posix
from fleet_rlm_clean.files.errors import AttachmentNotFoundError
from fleet_rlm_clean.files.models import AttachmentRef, StagedAttachment
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


async def assemble_turn_capabilities(
    context: RLMTurnContext,
    command: ChatTurnCommand,
    *,
    skill_registry: InMemorySkillRegistry | None = None,
    attachment_store: Any | None = None,
    volume_fs: Any | None = None,
    volume_paths: VolumePaths | None = None,
    max_artifact_bytes: int = 10 * 1024 * 1024,
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
    staged_attachments: tuple[StagedAttachment, ...] = ()

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
        refs: list[AttachmentRef] = []
        staged: list[StagedAttachment] = []
        paths = volume_paths or VolumePaths.from_mount(str(getattr(context.lease, "mount_path", "/home/daytona/fleet")))
        for attachment_id in command.attachment_ids:
            try:
                get_stored = getattr(attachment_store, "get_stored", None)
                if callable(get_stored) and volume_fs is not None:
                    stored_result = get_stored(
                        attachment_id,
                        user_id=command.user_id,
                        workspace_id=command.workspace_id,
                    )
                    stored = await stored_result if inspect.isawaitable(stored_result) else stored_result
                    ref = stored.ref
                    data = await asyncio.to_thread(volume_fs.read_bytes, stored.storage_ref)
                else:
                    result = attachment_store.get(
                        attachment_id,
                        user_id=command.user_id,
                        workspace_id=command.workspace_id,
                    )
                    ref = await result if inspect.isawaitable(result) else result
                    data_result = attachment_store.read_bytes(
                        attachment_id,
                        user_id=command.user_id,
                        workspace_id=command.workspace_id,
                    )
                    data = await data_result if inspect.isawaitable(data_result) else data_result
            except AttachmentNotFoundError as exc:
                raise AttachmentValidationError("invalid attachment reference") from exc
            refs.append(ref)
            if volume_fs is not None:
                sandbox_path = as_posix(
                    paths.run_attachment_file(
                        command.session_id,
                        context.run_id,
                        ref.id,
                        ref.filename,
                    )
                )
                await asyncio.to_thread(volume_fs.write_bytes, sandbox_path, data)
                staged.append(StagedAttachment(attachment_id=ref.id, sandbox_path=sandbox_path))
        attachments = tuple(refs)
        staged_attachments = tuple(staged)
    elif attachment_store is not None:
        attachments = ()

    if volume_fs is not None:
        file_host = FileToolHost(
            attachments=attachments,
            staged_attachments=staged_attachments,
            volume_fs=volume_fs,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            session_id=command.session_id,
            run_id=context.run_id,
            max_artifact_bytes=max_artifact_bytes,
            volume_paths=volume_paths,
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
        volume_fs=volume_fs,
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
        volume_fs: Any | None = None,
        volume_paths: VolumePaths | None = None,
        max_artifact_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._inner = inner
        self._skill_registry = skill_registry
        self._attachment_store = attachment_store
        self._volume_fs = volume_fs
        self._volume_paths = volume_paths
        self._max_artifact_bytes = max_artifact_bytes

    async def build(
        self,
        command: ChatTurnCommand,
        *,
        run_id: UUID | None = None,
    ) -> RLMTurnContext:
        build = getattr(self._inner, "build", None)
        if callable(build):
            base = build(command)
        elif callable(self._inner):
            base = self._inner(command)
        else:
            msg = "inner context builder must be callable or provide build(command)"
            raise TypeError(msg)
        if inspect.isawaitable(base):
            base = await base
        if run_id is not None:
            base = rebind_turn_context(base, run_id=run_id)
        return await assemble_turn_capabilities(
            base,
            command,
            skill_registry=self._skill_registry,
            attachment_store=self._attachment_store,
            volume_fs=self._volume_fs,
            volume_paths=self._volume_paths,
            max_artifact_bytes=self._max_artifact_bytes,
        )
