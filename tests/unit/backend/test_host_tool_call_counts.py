"""Host tools rely on native RLM execution bounds, not Fleet call counters."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import dspy
import pytest


def test_repeated_authorized_host_tool_calls_have_no_fleet_count_limit(tmp_path) -> None:
    from fleet_rlm.files.host_volume import HostVolumeMirror
    from fleet_rlm.files.models import AttachmentRef, StagedAttachment
    from fleet_rlm.files.tools import FileToolHost
    from fleet_rlm.files.volume_paths import VolumePaths
    from fleet_rlm.rlm.tool_observer import observe_tool
    from fleet_rlm.skills.catalog import SkillCatalog
    from fleet_rlm.skills.models import SkillCard, SkillDefinition, SkillResource
    from fleet_rlm.skills.tools import SkillToolHost

    user_id, workspace_id, session_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    skill = SkillDefinition(
        SkillCard(uuid4(), "repeatable", "repeatable authorized Skill", "1.0.0", True),
        "Inspect the authorized input.",
        {
            "references/private.md": SkillResource(
                "references/private.md", "text/markdown", "private skill resource body"
            )
        },
    )
    skill_host = SkillToolHost(SkillCatalog((skill,)))

    paths = VolumePaths.from_mount("/mnt/fleet")
    volume = HostVolumeMirror(tmp_path, volume_paths=paths)
    attachment_id = uuid4()
    attachment_path = f"/mnt/fleet/sessions/{session_id}/runs/{run_id}/input.txt"
    attachment_bytes = b"authorized input"
    volume.write_bytes(attachment_path, attachment_bytes)
    file_host = FileToolHost(
        attachments=(
            AttachmentRef(
                attachment_id,
                "input.txt",
                "text/plain",
                len(attachment_bytes),
                sha256(attachment_bytes).hexdigest(),
            ),
        ),
        staged_attachments=(StagedAttachment(attachment_id, attachment_path),),
        volume_fs=volume,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        max_artifact_bytes=1024,
        volume_paths=paths,
    )

    skill_results = [skill_host.load_skill(str(skill.card.id)) for _ in range(20)]
    attachment_results = [file_host.read_attachment(str(attachment_id)) for _ in range(20)]
    artifact_results = [file_host.create_artifact("text", f"result {index}") for index in range(20)]

    assert all(result["ok"] is True for result in skill_results)
    assert all(result["ok"] is True for result in attachment_results)
    assert all(result["ok"] is True for result in artifact_results)
    skill_events = skill_host.drain_public_events()
    assert [event["kind"] for event in skill_events] == ["skill.activated", "skill.loaded"]
    assert len(file_host.drain_public_events()) == 20
    assert len(file_host.drain_artifact_candidates()) == 20
    assert all(type(tool) is dspy.Tool for tool in (*skill_host.as_tools(), *file_host.as_tools()))
    assert set(skill_host.event_views()) == {"load_skill", "read_skill_resource"}
    assert set(file_host.event_views()) == {
        "read_attachment",
        "create_artifact",
        "publish_workspace_artifact",
    }

    file_tools = {str(tool.name): tool for tool in file_host.as_tools()}
    skill_tools = {str(tool.name): tool for tool in skill_host.as_tools()}
    assert file_tools["read_attachment"].args == {
        "attachment_id": {"type": "string"},
    }
    assert file_tools["read_attachment"].arg_types == {"attachment_id": str}
    assert "immutable authorized Attachment" in file_tools["read_attachment"].desc
    assert "only when" in file_tools["read_attachment"].desc
    assert file_tools["create_artifact"].args == {
        "kind": {"type": "string"},
        "content": {"type": "string"},
        "title": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
        },
    }
    assert file_tools["create_artifact"].arg_types == {
        "kind": str,
        "content": str,
        "title": str | None,
    }
    assert "promoted only by a successful Turn Commit" in file_tools["create_artifact"].desc
    assert skill_tools["load_skill"].args == {
        "skill_id": {"type": "string"},
        "expected_version": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
        },
    }
    assert skill_tools["load_skill"].arg_types == {
        "skill_id": str,
        "expected_version": str | None,
    }
    assert skill_tools["read_skill_resource"].args == {
        "skill_id": {"type": "string"},
        "resource_path": {"type": "string"},
        "expected_version": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
        },
    }
    assert skill_tools["read_skill_resource"].arg_types == {
        "skill_id": str,
        "resource_path": str,
        "expected_version": str | None,
    }
    observed: list[object] = []
    observe_tool(file_tools["read_attachment"], observed.append, file_host.event_views()["read_attachment"])(
        attachment_id=str(attachment_id)
    )
    observe_tool(file_tools["create_artifact"], observed.append, file_host.event_views()["create_artifact"])(
        kind="text",
        content="private artifact body",
        title="Report",
    )
    observe_tool(skill_tools["load_skill"], observed.append, skill_host.event_views()["load_skill"])(
        skill_id=str(skill.card.id)
    )
    observe_tool(
        skill_tools["read_skill_resource"],
        observed.append,
        skill_host.event_views()["read_skill_resource"],
    )(skill_id=str(skill.card.id), resource_path="references/private.md")

    assert observed[1].output["filename"] == "input.txt"
    assert observed[3].output == {"ok": True, "kind": "text", "title": "Report", "byte_size": 21}
    assert observed[5].output == {
        "ok": True,
        "skill_id": str(skill.card.id),
        "name": "repeatable",
        "version": "1.0.0",
    }
    assert observed[7].output == {
        "ok": True,
        "skill_id": str(skill.card.id),
        "path": "references/private.md",
        "encoding": "utf-8",
        "media_type": "text/markdown",
        "byte_size": 27,
    }
    serialized = str(observed)
    assert "authorized input" not in serialized
    assert "private artifact body" not in serialized
    assert "private-candidate" not in serialized
    assert "Inspect the authorized input" not in serialized
    assert "private skill resource body" not in serialized


@pytest.mark.asyncio
async def test_live_capability_teardown_removes_drained_artifact_candidate_bytes(tmp_path) -> None:
    from fleet_rlm.daytona.run_environment import LivePreparedCapabilities
    from fleet_rlm.files.host_volume import HostVolumeMirror
    from fleet_rlm.files.tools import FileToolHost
    from fleet_rlm.files.volume_paths import VolumePaths
    from fleet_rlm.rlm.context import RLMExecutionSpec

    user_id, workspace_id, session_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    paths = VolumePaths.from_mount("/mnt/fleet")
    volume = HostVolumeMirror(tmp_path, volume_paths=paths)
    files = FileToolHost(
        attachments=(),
        staged_attachments=(),
        volume_fs=volume,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        volume_paths=paths,
    )

    assert files.create_artifact("text", "uncommitted")["ok"] is True
    candidate = files.drain_artifact_candidates()[0]

    class Skills:
        def drain_public_events(self) -> list[dict[str, str]]:
            return []

    capabilities = LivePreparedCapabilities(RLMExecutionSpec(), files=files, skills=Skills())
    await capabilities.aclose()

    assert not volume.exists(candidate.staging_path)


def test_workspace_artifact_publication_reads_source_and_stages_only_a_candidate(tmp_path) -> None:
    from fleet_rlm.files.host_volume import HostVolumeMirror
    from fleet_rlm.files.tools import FileToolHost
    from fleet_rlm.files.volume_paths import VolumePaths

    user_id, workspace_id, session_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    paths = VolumePaths.from_mount("/mnt/fleet")
    volume = HostVolumeMirror(tmp_path, volume_paths=paths)
    source = paths.session_workspace_dir(session_id) / "report.md"
    body = b"# Durable report\n"
    volume.write_bytes(str(source), body)
    host = FileToolHost(
        attachments=(),
        staged_attachments=(),
        volume_fs=volume,
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        max_artifact_bytes=1024,
        volume_paths=paths,
    )

    result = host.publish_workspace_artifact(
        "report.md",
        "markdown",
        title="Report",
        expected_sha256=sha256(body).hexdigest(),
    )
    candidate = host.drain_artifact_candidates()[0]

    assert result["ok"] is True
    assert volume.read_bytes(str(source)) == body
    assert volume.read_bytes(candidate.staging_path) == body
    assert host.publish_workspace_artifact("report.md", "markdown", expected_sha256="0" * 64) == {
        "ok": False,
        "error": "checksum_mismatch",
    }
