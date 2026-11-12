from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SANDBOX_SKILL_HELPERS_CODE, SkillManager, parse_and_validate_skill_md


def test_skill_frontmatter_uses_yaml_and_preserves_colons_in_description() -> None:
    raw = """---
name: ledger-audit
description: "Audits ledgers: use when checking totals."
---

Check the total with Python.
"""

    metadata, body = parse_and_validate_skill_md("ledger-audit", raw)

    assert metadata == {
        "name": "ledger-audit",
        "description": "Audits ledgers: use when checking totals.",
    }
    assert body == "Check the total with Python."


def test_skill_frontmatter_rejects_folder_name_mismatch() -> None:
    raw = "---\nname: another-skill\ndescription: example\n---\n\nInstructions\n"

    with pytest.raises(ValueError, match="must match folder name"):
        parse_and_validate_skill_md("ledger-audit", raw)


def test_local_skill_helpers_support_progressive_disclosure_and_reject_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    helpers = {}
    source = SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})")
    exec(source, helpers)

    helpers["save_skill"](
        "ledger-audit",
        "Audit ledger totals when a reconciliation is requested.",
        "Read the source ledger and verify each total with Python.",
        scripts={"audit.py": "print('audit complete')\n"},
        references={"policy.md": "Amounts are stored in EUR.\n"},
    )
    assert helpers["list_skills"]() == [
        {"name": "ledger-audit", "description": "Audit ledger totals when a reconciliation is requested."}
    ]
    active_skill = helpers["use_skill"]("ledger-audit")
    assert "audit.py" in active_skill
    assert helpers["read_skill_reference"]("ledger-audit", "references/policy.md") == "Amounts are stored in EUR.\n"
    assert helpers["run_skill_script"]("ledger-audit", "scripts/audit.py") == "audit complete\n"

    outside = tmp_path / "outside.md"
    outside.write_text("not a Skill reference", encoding="utf-8")
    (root / "ledger-audit" / "references" / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        helpers["read_skill_reference"]("ledger-audit", "references/escape.md")


def test_local_skill_sync_prevalidates_all_skills_before_writing(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    valid = skills_root / "a-valid-skill"
    invalid = skills_root / "z-invalid-skill"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "SKILL.md").write_text("---\nname: a-valid-skill\ndescription: valid\n---\n\nInstructions.\n")
    (invalid / "SKILL.md").write_text("missing frontmatter\n")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []

        def write_workspace_file(self, relative_path: str, content: bytes) -> None:
            self.writes.append((relative_path, content))

    sandbox = RecordingSandbox()
    manager = SkillManager(sandbox, skills_root, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="frontmatter"):
        manager.sync_local_skills_to_volume()
    assert sandbox.writes == []


def test_local_catalog_matches_the_synced_skill_metadata(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "ledger-audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: ledger-audit\ndescription: "Audits ledger totals: on request."\n---\n\nCheck totals.\n',
        encoding="utf-8",
    )
    manager = SkillManager(object(), root, None)  # type: ignore[arg-type]

    assert manager.build_local_catalog_xml() == (
        "<available_skills>\n"
        '  <skill name="ledger-audit">Audits ledger totals: on request.</skill>\n'
        "</available_skills>"
    )


def test_local_skill_files_stage_bundled_and_local_resources_with_workspace_paths(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    local = tmp_path / "local"
    (bundled / "shared-skill").mkdir(parents=True)
    (bundled / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Bundled copy.\n---\n\nBundled.\n", encoding="utf-8"
    )
    (local / "shared-skill" / "references").mkdir(parents=True)
    skill_md = b"---\nname: shared-skill\ndescription: Local override.\n---\n\nLocal.\n"
    ref_md = b"Reference policy.\n"
    (local / "shared-skill" / "SKILL.md").write_bytes(skill_md)
    (local / "shared-skill" / "references" / "policy.md").write_bytes(ref_md)

    manager = SkillManager(object(), local, bundled)  # type: ignore[arg-type]
    assert manager.local_skill_files() == {
        "skills/shared-skill/SKILL.md": skill_md,
        "skills/shared-skill/references/policy.md": ref_md,
    }


def test_stage_local_skills_returns_catalog_and_files_in_one_scan(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local"
    (local / "audit-skill").mkdir(parents=True)
    skill_md = b"---\nname: audit-skill\ndescription: Single-pass stage.\n---\n\nCheck.\n"
    (local / "audit-skill" / "SKILL.md").write_bytes(skill_md)
    manager = SkillManager(object(), local, None)  # type: ignore[arg-type]
    scans = 0
    original = SkillManager._stage_local_skills

    def counted(self, **kwargs):
        nonlocal scans
        scans += 1
        return original(self, **kwargs)

    monkeypatch.setattr(SkillManager, "_stage_local_skills", counted)
    catalog, staged_files = manager.stage_local_skills()

    assert scans == 1
    assert 'name="audit-skill"' in catalog
    assert staged_files == {"skills/audit-skill/SKILL.md": skill_md}


def test_bundled_skill_discovery_and_activation_do_not_install_packages(tmp_path: Path, monkeypatch) -> None:
    bundled_skills = (
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    )
    root = tmp_path / "skills"
    shutil.copytree(BUNDLED_SKILLS_DIR, root)
    helpers: dict = {}
    exec(SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})"), helpers)

    def unexpected_process(*_args, **_kwargs):
        pytest.fail("Discovering or activating a Skill must not launch a process")

    monkeypatch.setattr(helpers["subprocess"], "run", unexpected_process)
    catalog = {item["name"] for item in helpers["list_skills"]()}
    assert catalog == set(bundled_skills)
    for name in bundled_skills:
        activated = helpers["use_skill"](name)
        assert f"# Skill: {name}" in activated
