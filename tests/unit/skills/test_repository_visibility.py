from __future__ import annotations

from fleet_rlm.skills.repository import list_visible
from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillScope, SkillVisibilityPolicy


def test_list_visible_returns_metadata_only() -> None:
    entries = list_visible(SkillRuntimeContext())
    assert entries
    assert all(not hasattr(entry, "instructions") for entry in entries)
    assert all(entry.description for entry in entries)


def test_list_visible_excludes_hidden_skill_ids() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["rlm"]),
    )
    names = {entry.name for entry in list_visible(context)}
    assert "rlm" not in names
    assert "sandbox-execution" in names


def test_list_visible_honors_allowlist() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(included_skill_ids=["rlm", "long-context"]),
    )
    names = {entry.name for entry in list_visible(context)}
    assert names <= {"rlm", "long-context"}


def test_list_visible_hides_non_visible_scopes() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(visible_scopes=[SkillScope.SCAFFOLD]),
    )
    entries = list_visible(context)
    assert entries
    assert all(entry.scope is SkillScope.SCAFFOLD for entry in entries)
