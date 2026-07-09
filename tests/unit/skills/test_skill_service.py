from __future__ import annotations

from pathlib import Path

from fleet_rlm.skills.loader import clear_skill_cache
from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy
from fleet_rlm.skills.service import (
    INACCESSIBLE_SKILL_MESSAGE,
    list_skills_output,
    load_skill_public_output,
    read_skill_resource_public_output,
    safe_source_label,
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


def test_list_skills_output_serializes_safe_catalog_items(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    output = list_skills_output(context=SkillRuntimeContext(volume_mount_path=str(volume)))

    assert output.status == "ok"
    alpha = next(item for item in output.skills if item.name == "alpha-route")
    assert alpha.source == "user"
    assert alpha.resource_count == 1
    assert str(volume) not in output.model_dump_json()


def test_load_skill_public_output_preserves_visible_bundle(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    output = load_skill_public_output("alpha-route", context=SkillRuntimeContext(volume_mount_path=str(volume)))

    assert output.status == "ok"
    assert output.name == "alpha-route"
    assert output.source == "user"
    assert output.resources


def test_load_skill_public_output_sanitizes_hidden_skill() -> None:
    clear_skill_cache()
    context = SkillRuntimeContext(visibility=SkillVisibilityPolicy(excluded_skill_ids=["diagnostics"]))

    output = load_skill_public_output("diagnostics", context=context)

    assert output.status == "not_found"
    assert output.name == ""
    assert output.error == INACCESSIBLE_SKILL_MESSAGE
    assert "diagnostics" not in output.model_dump_json()


def test_read_skill_resource_public_output_sanitizes_hidden_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["alpha-route"]),
    )

    output = read_skill_resource_public_output("alpha-route", "references/note.md", context=context)

    assert output.status == "not_found"
    assert output.name == ""
    assert output.path == ""
    assert output.error == INACCESSIBLE_SKILL_MESSAGE
    assert output.code == "skill_not_found"
    assert "alpha-route" not in output.model_dump_json()


def test_safe_source_label_strips_backing_path() -> None:
    assert safe_source_label("user:/home/daytona/memory/skills/user/alpha-route") == "user"
