"""Host tools rely on native RLM execution bounds, not Fleet call counters."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4


def test_repeated_authorized_host_tool_calls_have_no_fleet_count_limit(tmp_path) -> None:
    from fleet_rlm.daytona.paths import VolumePaths
    from fleet_rlm.daytona.volume_fs import HostVolumeMirror
    from fleet_rlm.files.models import AttachmentRef, StagedAttachment
    from fleet_rlm.files.tools import FileToolHost
    from fleet_rlm.skills.authorize import SkillAuthorizer
    from fleet_rlm.skills.registry import InMemorySkillRegistry
    from fleet_rlm.skills.tools import SkillToolHost

    user_id, workspace_id, session_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    registry = InMemorySkillRegistry()
    skill = registry.register(
        name="repeatable",
        description="repeatable authorized Skill",
        instructions="Inspect the authorized input.",
    )
    skill_host = SkillToolHost(
        SkillAuthorizer(registry),
        user_id=user_id,
        workspace_id=workspace_id,
    )

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

    skill_results = [skill_host.load_skill(str(skill.id)) for _ in range(20)]
    attachment_results = [file_host.read_attachment(str(attachment_id)) for _ in range(20)]
    artifact_results = [file_host.create_artifact("text", f"result {index}") for index in range(20)]

    assert all(result["ok"] is True for result in skill_results)
    assert all(result["ok"] is True for result in attachment_results)
    assert all(result["ok"] is True for result in artifact_results)
    assert len(skill_host.drain_public_events()) == 20
    assert len(file_host.drain_public_events()) == 20
    assert len(file_host.drain_artifact_candidates()) == 20
