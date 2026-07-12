"""Turn capability assembly: Skill/File hosts + attachment validation (residual #1)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from fleet_rlm_clean.chat.capabilities import (
    AttachmentValidationError,
    CapabilityContextBuilder,
    assemble_turn_capabilities,
    validate_attachment_ids,
)
from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.context_builder import OfflineContextBuilder, rebind_turn_context
from fleet_rlm_clean.files.uploads import LocalAttachmentStore
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.skills.registry import InMemorySkillRegistry


def test_validate_attachment_ids_rejects_foreign_or_missing(tmp_path: Path) -> None:
    store = LocalAttachmentStore(tmp_path / "up", max_bytes=1024)
    user, ws = uuid4(), uuid4()
    other = uuid4()
    ref = store.upload(
        user_id=user,
        workspace_id=ws,
        filename="a.txt",
        content_type="text/plain",
        data=b"hello",
    )
    ok = validate_attachment_ids(
        store,
        (ref.id,),
        user_id=user,
        workspace_id=ws,
    )
    assert len(ok) == 1
    assert ok[0].id == ref.id

    with pytest.raises(AttachmentValidationError):
        validate_attachment_ids(
            store,
            (ref.id,),
            user_id=user,
            workspace_id=other,
        )
    with pytest.raises(AttachmentValidationError):
        validate_attachment_ids(
            store,
            (uuid4(),),
            user_id=user,
            workspace_id=ws,
        )


@pytest.mark.asyncio
async def test_assemble_turn_capabilities_binds_skill_and_file_tools(tmp_path: Path) -> None:
    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="cap-skill",
        description="for assembly",
        instructions="BODY SECRET",
        version="1.0.0",
    )
    att = LocalAttachmentStore(tmp_path / "up", max_bytes=1024)
    from fleet_rlm_clean.daytona.paths import VolumePaths
    from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror

    paths = VolumePaths.from_mount()
    volume_fs = HostVolumeMirror(tmp_path / "volume", volume_paths=paths)
    user, ws = uuid4(), uuid4()
    session_id, run_id = uuid4(), uuid4()
    ref = att.upload(
        user_id=user,
        workspace_id=ws,
        filename="note.txt",
        content_type="text/plain",
        data=b"payload",
    )
    command = ChatTurnCommand(
        user_id=user,
        workspace_id=ws,
        session_id=session_id,
        message="use skills and files",
        attachment_ids=(ref.id,),
    )
    base = OfflineContextBuilder(budget=RLMBudget(max_iterations=3, max_llm_calls=4, max_output_chars=500)).build(
        command
    )
    base = rebind_turn_context(base, run_id=run_id)

    enriched = await assemble_turn_capabilities(
        base,
        command,
        skill_registry=registry,
        attachment_store=att,
        volume_fs=volume_fs,
        volume_paths=paths,
    )
    assert skill.id in {c.id for c in enriched.skill_cards}
    assert enriched.skill_tool_host is not None
    assert enriched.file_tool_host is not None
    names = {getattr(t, "__name__", "") for t in enriched.tools}
    assert "load_skill" in names
    assert "read_attachment" in names
    assert "create_artifact" in names
    assert len(enriched.attachments) == 1
    # Card never carries body
    for card in enriched.skill_cards:
        assert not hasattr(card, "instructions")


@pytest.mark.asyncio
async def test_assemble_without_stores_is_skills_only() -> None:
    registry = InMemorySkillRegistry()
    registry.register(name="only", description="d", instructions="body")
    command = ChatTurnCommand(
        user_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        message="hi",
    )
    base = OfflineContextBuilder().build(command)
    enriched = await assemble_turn_capabilities(base, command, skill_registry=registry)
    assert enriched.skill_tool_host is not None
    assert enriched.file_tool_host is None
    assert any(getattr(t, "__name__", "") == "load_skill" for t in enriched.tools)


@pytest.mark.asyncio
async def test_capabilities_bind_staging_and_candidates_to_claimed_run_id(tmp_path: Path) -> None:
    from fleet_rlm_clean.daytona.paths import VolumePaths
    from fleet_rlm_clean.daytona.volume_fs import HostVolumeMirror

    paths = VolumePaths.from_mount()
    volume_fs = HostVolumeMirror(tmp_path / "volume", volume_paths=paths)
    store = LocalAttachmentStore(
        tmp_path / "uploads",
        max_bytes=1024,
        volume_fs=volume_fs,
        volume_paths=paths,
    )
    user_id, workspace_id, session_id, claimed_run_id = (uuid4() for _ in range(4))
    attachment = store.upload(
        user_id=user_id,
        workspace_id=workspace_id,
        filename="claimed.txt",
        content_type="text/plain",
        data=b"claimed",
    )
    command = ChatTurnCommand(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        message="create",
        attachment_ids=(attachment.id,),
    )
    builder = CapabilityContextBuilder(
        OfflineContextBuilder(),
        attachment_store=store,
        volume_fs=volume_fs,
        volume_paths=paths,
    )

    context = await builder.build(command, run_id=claimed_run_id)
    assert context.run_id == claimed_run_id
    assert context.file_tool_host is not None
    created = context.file_tool_host.create_artifact("text", "candidate")
    assert created["ok"] is True
    (candidate,) = context.file_tool_host.drain_artifact_candidates()
    assert candidate.run_id == claimed_run_id
    assert str(claimed_run_id) in candidate.staging_path
