from __future__ import annotations

from pathlib import Path

from fleet_rlm.skills.catalog import iter_all_skill_metadata
from fleet_rlm.skills.provenance import write_provenance
from fleet_rlm.skills.schemas import (
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
)


def _write_skill_md(path: Path, *, name: str, description: str) -> None:
    path.write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def test_iter_all_skill_metadata_includes_scaffold_directory_skill() -> None:
    names = {metadata.name for metadata in iter_all_skill_metadata(SkillRuntimeContext())}
    assert "rlm" in names
    assert "sandbox-execution" in names


def test_volume_directory_and_legacy_flat_skills_are_discovered(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    system_dir = skills_root / "system"
    system_dir.mkdir(parents=True)

    directory_skill = system_dir / "custom-dir"
    directory_skill.mkdir()
    _write_skill_md(
        directory_skill / "SKILL.md",
        name="custom-dir",
        description="Directory-style custom skill.",
    )
    (directory_skill / "references").mkdir()
    (directory_skill / "references" / "note.md").write_text("note", encoding="utf-8")

    _write_skill_md(
        system_dir / "legacy-flat.md",
        name="legacy-flat",
        description="Legacy flat markdown skill.",
    )

    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    by_name = {metadata.name: metadata for metadata in iter_all_skill_metadata(context)}
    assert "custom-dir" in by_name
    assert by_name["custom-dir"].directory_style is True
    assert "legacy-flat" in by_name
    assert by_name["legacy-flat"].directory_style is False


def test_user_scope_overrides_system_and_scaffold_for_same_name(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    user_dir = skills_root / "user" / "shared-name"
    system_dir = skills_root / "system" / "shared-name"
    user_dir.mkdir(parents=True)
    system_dir.mkdir(parents=True)
    _write_skill_md(user_dir / "SKILL.md", name="shared-name", description="User wins.")
    _write_skill_md(system_dir / "SKILL.md", name="shared-name", description="System loses.")

    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    metadata = next(metadata for metadata in iter_all_skill_metadata(context) if metadata.name == "shared-name")
    assert metadata.scope is SkillScope.USER
    assert metadata.description == "User wins."


def test_missing_session_project_org_dirs_are_skipped(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "system").mkdir(parents=True)
    _write_skill_md(
        skills_root / "system" / "only-system.md",
        name="only-system",
        description="System skill only.",
    )
    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    scopes = {metadata.scope for metadata in iter_all_skill_metadata(context) if metadata.name == "only-system"}
    assert scopes == {SkillScope.SYSTEM}


def test_remote_source_without_provenance_is_community_trust(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    skills_root = tmp_path / "skills"
    user_dir = skills_root / "user" / "remote-only"
    user_dir.mkdir(parents=True)
    _write_skill_md(user_dir / "SKILL.md", name="remote-only", description="Remote source label only.")
    write_provenance(
        str(tmp_path),
        SkillProvenanceRecord(
            skill_name="remote-only",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash="hash",
            installed_at=datetime.now(UTC).isoformat(),
        ),
    )
    context = SkillRuntimeContext(volume_mount_path=str(tmp_path))
    metadata = next(item for item in iter_all_skill_metadata(context) if item.name == "remote-only")
    assert metadata.trust_level is SkillTrustLevel.COMMUNITY
