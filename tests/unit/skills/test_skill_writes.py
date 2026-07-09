from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.skills.audit import read_audit_events
from fleet_rlm.skills.errors import (
    SkillProtectedError,
    SkillValidationError,
    SkillWriteDeniedError,
    StagedChangeNotFoundError,
)
from fleet_rlm.skills.loader import clear_skill_cache
from fleet_rlm.skills.repository import list_visible
from fleet_rlm.skills.schemas import (
    SkillRuntimeContext,
    SkillScope,
    SkillVisibilityPolicy,
    SkillWriteAction,
    SkillWriteContext,
    SkillWritePolicy,
)
from fleet_rlm.skills.service import (
    INACCESSIBLE_SKILL_MESSAGE,
    list_skills_output,
    load_skill_public_output,
    public_error_for_skill_error,
)
from fleet_rlm.skills.validator import validate_skill_markdown, validate_write_target_path
from fleet_rlm.skills.writes import (
    approve_staged_skill_change,
    reject_staged_skill_change,
    stage_skill_change,
    write_skill_for_scope,
)
from fleet_rlm.tools.skill_tools import load_skill_tool_impl


def _skill_markdown(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n'


def _write_context(volume: Path, **kwargs) -> SkillWriteContext:
    return SkillWriteContext(volume_mount_path=str(volume), user_id="user-1", session_id="session-1", **kwargs)


def _write_directory_skill(volume: Path, scope: SkillScope, name: str, description: str) -> None:
    skill_dir = volume / "skills" / scope.value / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(_skill_markdown(name, description), encoding="utf-8")


def _create_user_skill(
    volume: Path,
    name: str,
    raw_markdown: str,
    **context_kwargs,
):
    reason = context_kwargs.pop("reason", None)
    return write_skill_for_scope(
        scope=SkillScope.USER,
        action=SkillWriteAction.CREATE,
        name=name,
        raw_markdown=raw_markdown,
        context=_write_context(volume, **context_kwargs),
        reason=reason,
    )


def _delete_user_skill(volume: Path, name: str, **context_kwargs):
    return write_skill_for_scope(
        scope=SkillScope.USER,
        action=SkillWriteAction.DELETE,
        name=name,
        raw_markdown=None,
        context=_write_context(volume, **context_kwargs),
    )


def test_create_user_skill_commits_when_staging_not_required(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    result = _create_user_skill(volume, "draft-skill", _skill_markdown("draft-skill", "Draft skill content."))

    assert result is None
    skill_md = volume / "skills" / "user" / "draft-skill" / "SKILL.md"
    assert skill_md.is_file()
    events = read_audit_events(str(volume))
    assert events[-1].action.value == "create"
    assert events[-1].new_content_hash


def test_create_session_skill_stages_for_agent_writes(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume, actor="agent")

    staged = write_skill_for_scope(
        scope=SkillScope.SESSION,
        action=SkillWriteAction.CREATE,
        name="session-skill",
        raw_markdown=_skill_markdown("session-skill", "Session scoped skill."),
        context=context,
    )

    assert staged is not None
    assert staged.status.value == "pending"
    assert not (volume / "skills" / "session" / "session-skill" / "SKILL.md").exists()
    assert (volume / "skills" / ".staging" / staged.id / "SKILL.md").is_file()


def test_create_reason_is_recorded_for_staged_and_approved_writes(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume, actor="agent")

    staged = _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        actor="agent",
        reason="draft from chat",
    )
    assert staged is not None
    assert staged.reason == "draft from chat"

    approve_staged_skill_change(staged.id, context)

    events = read_audit_events(str(volume))
    assert any(event.action.value == "stage" and event.reason == "draft from chat" for event in events)
    assert any(event.action.value == "create" and event.reason == "draft from chat" for event in events)


def test_system_scope_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume)

    with pytest.raises(SkillWriteDeniedError):
        write_skill_for_scope(
            scope=SkillScope.SYSTEM,
            action=SkillWriteAction.CREATE,
            name="system-skill",
            raw_markdown=_skill_markdown("system-skill", "System skill."),
            context=context,
        )


def test_scaffold_scope_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume)

    with pytest.raises(SkillWriteDeniedError):
        write_skill_for_scope(
            scope=SkillScope.SCAFFOLD,
            action=SkillWriteAction.CREATE,
            name="scaffold-skill",
            raw_markdown=_skill_markdown("scaffold-skill", "Scaffold skill."),
            context=context,
        )


def test_invalid_skill_markdown_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    with pytest.raises(SkillValidationError):
        _create_user_skill(volume, "not valid", _skill_markdown("not valid", "Invalid name."))


def test_missing_description_is_rejected(tmp_path: Path) -> None:
    result = validate_skill_markdown("---\nname: draft-skill\n---\n\n# Draft\n", directory_name="draft-skill")
    assert result.valid is False
    assert any(issue.code == "missing_description" for issue in result.issues)


def test_non_kebab_case_name_is_rejected(tmp_path: Path) -> None:
    result = validate_skill_markdown(
        _skill_markdown("Bad_Name", "Invalid casing."),
        directory_name="Bad_Name",
    )
    assert result.valid is False
    assert any(issue.code == "invalid_name" for issue in result.issues)


def test_path_traversal_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    with pytest.raises(SkillValidationError) as exc_info:
        _create_user_skill(volume, "../escape", _skill_markdown("escape", "Traversal escape."))
    assert exc_info.value.code == "invalid_skill_name"


def test_absolute_path_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    scope_root = volume / "skills" / "user"
    scope_root.mkdir(parents=True)
    result = validate_write_target_path(scope_root, Path("/etc/passwd"))
    assert result.valid is False
    assert any(issue.code == "absolute_path" for issue in result.issues)


def test_backslash_traversal_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    scope_root = volume / "skills" / "user"
    scope_root.mkdir(parents=True)
    result = validate_write_target_path(scope_root, scope_root / "evil\\skill")
    assert result.valid is False
    assert any(issue.code == "backslash_path" for issue in result.issues)


def test_symlink_escape_write_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    scope_root = volume / "skills" / "user"
    scope_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = scope_root / "linked-skill"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    result = validate_write_target_path(scope_root, link)
    assert result.valid is False
    assert any(issue.code == "outside_scope_root" for issue in result.issues)


def test_protected_system_skill_overwrite_is_rejected(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.SYSTEM, "protected-skill", "Protected system skill.")
    context = _write_context(volume)

    with pytest.raises(SkillWriteDeniedError):
        write_skill_for_scope(
            scope=SkillScope.SYSTEM,
            action=SkillWriteAction.UPDATE,
            name="protected-skill",
            raw_markdown=_skill_markdown("protected-skill", "Attempted overwrite."),
            context=context,
        )


def test_approval_commits_staged_change(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume, actor="agent")
    staged = _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        actor="agent",
    )
    assert staged is not None

    approve_staged_skill_change(staged.id, context)

    skill_md = volume / "skills" / "user" / "draft-skill" / "SKILL.md"
    assert skill_md.is_file()
    events = read_audit_events(str(volume))
    assert any(event.action.value == "approve" for event in events)


def test_rejection_does_not_commit_staged_change(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume, actor="agent")
    staged = _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        actor="agent",
    )
    assert staged is not None

    reject_staged_skill_change(staged.id, context, reason="not ready")

    assert not (volume / "skills" / "user" / "draft-skill" / "SKILL.md").exists()
    events = read_audit_events(str(volume))
    assert events[-1].action.value == "reject"
    assert events[-1].reason == "not ready"


def test_audit_metadata_records_actor_action_hash_timestamp(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        workspace_id="ws-1",
        org_id="org-1",
    )

    event = read_audit_events(str(volume))[-1]
    assert event.actor == "user"
    assert event.user_id == "user-1"
    assert event.session_id == "session-1"
    assert event.workspace_id == "ws-1"
    assert event.org_id == "org-1"
    assert event.action.value == "create"
    assert event.timestamp
    assert event.new_content_hash


def test_hidden_skill_write_attempt_returns_sanitized_public_error(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "hidden-skill", "Hidden skill.")
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["hidden-skill"]),
    )
    output = load_skill_public_output("hidden-skill", context=context)
    assert output.error == INACCESSIBLE_SKILL_MESSAGE

    protected_error = public_error_for_skill_error(SkillProtectedError())
    assert protected_error.message == INACCESSIBLE_SKILL_MESSAGE
    assert "protected" not in protected_error.message.lower()


def test_existing_read_only_list_still_works(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "alpha-route", "Alpha route support.")
    clear_skill_cache()

    output = list_skills_output(context=SkillRuntimeContext(volume_mount_path=str(volume)))
    names = {item.name for item in output.skills}
    assert "alpha-route" in names
    assert "rlm" in names


def test_existing_read_only_list_visible_still_works(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "alpha-route", "Alpha route support.")

    entries = list_visible(SkillRuntimeContext(volume_mount_path=str(volume)))
    assert any(entry.name == "alpha-route" for entry in entries)


def test_runtime_load_skill_tool_still_works(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "alpha-route", "Alpha route support.")
    clear_skill_cache()

    output = load_skill_tool_impl("alpha-route", context=SkillRuntimeContext(volume_mount_path=str(volume)))
    assert output["status"] == "ok"
    assert output["name"] == "alpha-route"


def test_user_writes_can_be_forced_to_stage(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    staged = _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        policy=SkillWritePolicy(require_staging=True),
    )

    assert staged is not None
    assert not (volume / "skills" / "user" / "draft-skill" / "SKILL.md").exists()


def test_stage_skill_change_supports_delete(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "draft-skill", "Draft skill content.")
    context = _write_context(volume, actor="agent")

    staged = stage_skill_change(
        scope=SkillScope.USER,
        action=SkillWriteAction.DELETE,
        name="draft-skill",
        raw_markdown=None,
        context=context,
    )

    assert staged.action.value == "delete"
    assert (volume / "skills" / "user" / "draft-skill" / "SKILL.md").is_file()


def test_delete_user_skill_commits_when_allowed(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "draft-skill", "Draft skill content.")

    _delete_user_skill(volume, "draft-skill")

    assert not (volume / "skills" / "user" / "draft-skill").exists()


def test_create_user_skill_cannot_shadow_builtin_scaffold_name(tmp_path: Path) -> None:
    volume = tmp_path / "memory"

    with pytest.raises(SkillProtectedError):
        _create_user_skill(volume, "rlm", _skill_markdown("rlm", "Attempted builtin shadow."))


def test_create_session_skill_cannot_shadow_existing_user_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, SkillScope.USER, "shared-skill", "User scoped skill.")

    with pytest.raises(SkillProtectedError):
        write_skill_for_scope(
            scope=SkillScope.SESSION,
            action=SkillWriteAction.CREATE,
            name="shared-skill",
            raw_markdown=_skill_markdown("shared-skill", "Session scoped shadow."),
            context=_write_context(volume),
        )


def test_approve_rejects_foreign_workspace(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    creator = _write_context(volume, actor="agent", workspace_id="ws-1")
    staged = write_skill_for_scope(
        scope=SkillScope.USER,
        action=SkillWriteAction.CREATE,
        name="draft-skill",
        raw_markdown=_skill_markdown("draft-skill", "Draft skill content."),
        context=creator,
    )
    assert staged is not None

    foreign = _write_context(volume, workspace_id="ws-2")
    with pytest.raises(StagedChangeNotFoundError):
        approve_staged_skill_change(staged.id, foreign)


def test_double_approve_is_rejected_after_first_commit(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    context = _write_context(volume, actor="agent")
    staged = _create_user_skill(
        volume,
        "draft-skill",
        _skill_markdown("draft-skill", "Draft skill content."),
        actor="agent",
    )
    assert staged is not None

    approve_staged_skill_change(staged.id, context)

    with pytest.raises(StagedChangeNotFoundError):
        approve_staged_skill_change(staged.id, context)
