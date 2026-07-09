from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy
from fleet_rlm.tools.skill_tools import (
    list_skills_impl,
    load_skill_tool_impl,
    read_skill_resource_impl,
)


def _write_directory_skill(volume: Path, name: str, description: str, *, reference_body: str = "reference") -> None:
    skill_dir = volume / "skills" / "user" / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )
    refs = skill_dir / "references"
    refs.mkdir()
    refs.joinpath("note.md").write_text(reference_body, encoding="utf-8")


def _write_flat_skill(volume: Path, name: str, description: str) -> None:
    skill_dir = volume / "skills" / "user"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath(f"{name}.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def test_list_skills_returns_visible_skills_only() -> None:
    payload = list_skills_impl()

    assert payload["status"] == "ok"
    names = {item["name"] for item in payload["skills"]}
    assert "rlm" in names
    for item in payload["skills"]:
        assert "instructions" not in item
        assert "content" not in item


def test_list_skills_does_not_return_hidden_skills() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["rlm"]),
    )

    payload = list_skills_impl(context=context)

    names = {item["name"] for item in payload["skills"]}
    assert "rlm" not in names


def test_load_skill_works_for_visible_scaffold_skill() -> None:
    payload = load_skill_tool_impl("rlm")

    assert payload["status"] == "ok"
    assert payload["name"] == "rlm"
    assert payload["instructions"].startswith("---")
    assert payload["resources"]
    assert payload["source"] == "scaffold"
    assert all("content" not in resource for resource in payload["resources"])


def test_load_skill_works_for_visible_volume_directory_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    payload = load_skill_tool_impl(
        "alpha-route",
        context=SkillRuntimeContext(volume_mount_path=str(volume)),
    )

    assert payload["status"] == "ok"
    assert payload["name"] == "alpha-route"
    assert "alpha-route" in payload["instructions"]
    assert payload["resources"]
    assert payload["source"] == "user"


def test_load_skill_works_for_legacy_flat_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_flat_skill(volume, "legacy-flat", "Legacy flat markdown skill.")

    payload = load_skill_tool_impl(
        "legacy-flat",
        context=SkillRuntimeContext(volume_mount_path=str(volume)),
    )

    assert payload["status"] == "ok"
    assert payload["name"] == "legacy-flat"
    assert payload["resources"] == []


def test_load_skill_rejects_hidden_skill_safely() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["diagnostics"]),
    )

    payload = load_skill_tool_impl("diagnostics", context=context)

    assert payload["status"] == "not_found"
    assert payload["error"] == "Skill not found or inaccessible."
    assert "diagnostics" not in str(payload)


def test_read_skill_resource_works_for_safe_visible_reference(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(
        volume,
        "alpha-route",
        "Zephyr alpha routing support.",
        reference_body="reference body",
    )

    payload = read_skill_resource_impl(
        "alpha-route",
        "references/note.md",
        context=SkillRuntimeContext(volume_mount_path=str(volume)),
    )

    assert payload["status"] == "ok"
    assert payload["content"] == "reference body"


def test_read_skill_resource_rejects_hidden_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["alpha-route"]),
    )

    payload = read_skill_resource_impl("alpha-route", "references/note.md", context=context)

    assert payload["status"] == "not_found"
    assert payload["error"] == "Skill not found or inaccessible."


def test_read_skill_resource_rejects_traversal() -> None:
    payload = read_skill_resource_impl("rlm", "../SKILL.md")

    assert payload["status"] == "error"
    assert payload["code"] == "invalid_resource_path"


def test_read_skill_resource_rejects_absolute_path() -> None:
    payload = read_skill_resource_impl("rlm", "/etc/passwd")

    assert payload["status"] == "error"
    assert payload["code"] == "invalid_resource_path"


def test_read_skill_resource_rejects_symlink_escape(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = volume / "skills" / "user" / "alpha-route" / "references" / "outside.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    payload = read_skill_resource_impl(
        "alpha-route",
        "references/outside.md",
        context=SkillRuntimeContext(volume_mount_path=str(volume)),
    )

    assert payload["status"] == "error"
    assert payload["code"] == "invalid_resource_path"


def test_compatibility_import_runtime_tools_skill_tools_load_skill() -> None:
    import importlib

    importlib.import_module("fleet_rlm.tools.skill_tools")
    from fleet_rlm.runtime.tools import skill_tools as runtime_skill_tools
    from fleet_rlm.tools import skill_tools as canonical_skill_tools

    assert runtime_skill_tools.load_skill is canonical_skill_tools.load_skill


def test_canonical_skill_tools_imports_without_prior_skills_package_init() -> None:
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fleet_rlm.tools.skill_tools as module; "
                "assert callable(module.load_skill); "
                "assert callable(module.list_skills); "
                "assert callable(module.read_skill_resource)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_load_skill_tool_rejects_invisible_skill_via_stub() -> None:
    from fleet_rlm.tools.skill_tools import load_skill

    with patch(
        "fleet_rlm.tools.skill_tools.default_skill_runtime_context",
        return_value=SkillRuntimeContext(
            visibility=SkillVisibilityPolicy(excluded_skill_ids=["diagnostics"]),
        ),
    ):
        payload = load_skill("diagnostics")

    assert payload["status"] == "not_found"
    assert payload["error"] == "Skill not found or inaccessible."
    assert "diagnostics" not in str(payload)
