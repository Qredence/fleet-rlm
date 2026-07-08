from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.skills.loader import load_resource, load_skill_bundle
from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy


def _write_skill_md(path: Path, *, name: str, description: str) -> None:
    path.write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def test_load_skill_bundle_inventories_resources_without_reading_contents() -> None:
    bundle = load_skill_bundle("rlm", SkillRuntimeContext())
    assert bundle.instructions.startswith("---")
    assert bundle.resources
    assert all(resource.path for resource in bundle.resources)


def test_load_resource_reads_scaffold_reference() -> None:
    content = load_resource("rlm", "references/architecture.md", SkillRuntimeContext())
    assert "architecture" in content.lower() or content.strip()


def test_load_resource_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        load_resource("rlm", "../SKILL.md", SkillRuntimeContext())


def test_load_resource_rejects_invisible_skill() -> None:
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["rlm"]),
    )
    with pytest.raises(ValueError, match="not visible"):
        load_resource("rlm", "references/architecture.md", context)


def test_legacy_flat_skill_bundle_has_empty_resources(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills" / "user"
    skills_root.mkdir(parents=True)
    _write_skill_md(
        skills_root / "legacy-flat.md",
        name="legacy-flat",
        description="Legacy flat markdown skill.",
    )
    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    bundle = load_skill_bundle("legacy-flat", context)
    assert bundle.metadata.directory_style is False
    assert bundle.resources == []


def test_load_resource_rejects_legacy_flat_skill_resources(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills" / "user"
    skills_root.mkdir(parents=True)
    _write_skill_md(
        skills_root / "legacy-flat.md",
        name="legacy-flat",
        description="Legacy flat markdown skill.",
    )
    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    with pytest.raises(ValueError, match="no resource directory"):
        load_resource("legacy-flat", "references/note.md", context)
