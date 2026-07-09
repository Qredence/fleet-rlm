from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fleet_rlm.skills.catalog import iter_all_skill_metadata
from fleet_rlm.skills.provenance import (
    content_hash_for_markdown,
    content_hash_for_skill_dir,
    provenance_path,
    read_provenance,
    write_provenance,
)
from fleet_rlm.skills.schemas import (
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
)


def _markdown(name: str, description: str) -> str:
    return f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n'


def test_content_hash_for_markdown_is_stable() -> None:
    content = _markdown("alpha", "Alpha skill.")
    assert content_hash_for_markdown(content) == content_hash_for_markdown(content)


def test_content_hash_for_skill_dir_changes_when_file_changes(tmp_path: Path) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    skill_dir.joinpath("SKILL.md").write_text(_markdown("alpha", "Alpha skill."), encoding="utf-8")
    first = content_hash_for_skill_dir(skill_dir)
    skill_dir.joinpath("references").mkdir()
    skill_dir.joinpath("references", "note.md").write_text("note", encoding="utf-8")
    second = content_hash_for_skill_dir(skill_dir)
    assert first != second


def test_provenance_round_trip(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    now = datetime.now(UTC).isoformat()
    record = SkillProvenanceRecord(
        skill_name="remote-skill",
        scope=SkillScope.USER,
        source=SkillInstallSource.URL_SINGLE,
        source_url="https://example.com/SKILL.md",
        trust_level=SkillTrustLevel.COMMUNITY,
        content_hash="abc123",
        installed_at=now,
    )
    write_provenance(str(volume), record)
    path = provenance_path(str(volume), SkillScope.USER, "remote-skill")
    assert path.is_file()
    loaded = read_provenance(str(volume), SkillScope.USER, "remote-skill")
    assert loaded is not None
    assert loaded.source_url == "https://example.com/SKILL.md"
    assert loaded.trust_level is SkillTrustLevel.COMMUNITY


def test_catalog_overlays_community_trust_from_provenance(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    skill_dir = volume / "skills" / "user" / "remote-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        _markdown("remote-skill", "Remote community skill."),
        encoding="utf-8",
    )
    now = datetime.now(UTC).isoformat()
    write_provenance(
        str(volume),
        SkillProvenanceRecord(
            skill_name="remote-skill",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash=content_hash_for_skill_dir(skill_dir),
            installed_at=now,
        ),
    )

    context = SkillRuntimeContext(volume_mount_path=str(volume))
    metadata = next(item for item in iter_all_skill_metadata(context) if item.name == "remote-skill")
    assert metadata.trust_level is SkillTrustLevel.COMMUNITY


def test_catalog_keeps_trusted_without_provenance(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    skill_dir = volume / "skills" / "user" / "local-skill"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        _markdown("local-skill", "Local user skill."),
        encoding="utf-8",
    )

    context = SkillRuntimeContext(volume_mount_path=str(volume))
    metadata = next(item for item in iter_all_skill_metadata(context) if item.name == "local-skill")
    assert metadata.trust_level is SkillTrustLevel.TRUSTED
