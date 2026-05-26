from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl


@pytest.fixture()
def vol(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "system").mkdir(parents=True)
    (tmp_path / "skills" / "user").mkdir(parents=True)
    return tmp_path


def test_load_system_skill(vol: Path) -> None:
    (vol / "skills" / "system" / "my-skill.md").write_text("# My Skill\nDo things.", encoding="utf-8")
    result = _load_skill_impl("my-skill", volume_mount_path=str(vol))
    assert result.status == "ok"
    assert result.scope == "system"
    assert "My Skill" in result.instructions


def test_load_user_skill_priority(vol: Path) -> None:
    (vol / "skills" / "system" / "foo.md").write_text("system version", encoding="utf-8")
    (vol / "skills" / "user" / "foo.md").write_text("user version", encoding="utf-8")
    result = _load_skill_impl("foo", volume_mount_path=str(vol))
    assert result.status == "ok"
    assert result.scope == "user"
    assert result.instructions == "user version"


def test_load_skill_with_md_suffix_stripped(vol: Path) -> None:
    (vol / "skills" / "system" / "my-skill.md").write_text("content", encoding="utf-8")
    result = _load_skill_impl("my-skill.md", volume_mount_path=str(vol))
    assert result.status == "ok"
    assert result.name == "my-skill"


def test_load_skill_not_found(vol: Path) -> None:
    result = _load_skill_impl("nonexistent", volume_mount_path=str(vol))
    assert result.status == "not_found"


def test_load_skill_path_traversal_blocked(vol: Path) -> None:
    result = _load_skill_impl("../etc/passwd", volume_mount_path=str(vol))
    assert result.status == "error"


def test_load_skill_no_volume() -> None:
    result = _load_skill_impl("some-skill", volume_mount_path=None)
    assert result.status == "error"


def test_load_skill_empty_name(vol: Path) -> None:
    result = _load_skill_impl("", volume_mount_path=str(vol))
    assert result.status == "error"
