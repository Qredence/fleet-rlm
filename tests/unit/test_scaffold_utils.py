from __future__ import annotations

from pathlib import Path

from fleet_rlm.utils import scaffold


def _write_markdown(path: Path, body: str = "# stub\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_get_scaffold_dir_prefers_complete_tracked_source(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "fleet_rlm"
    source_scaffold = package_root / "features" / "scaffold"
    legacy_scaffold = package_root / "_scaffold"

    _write_markdown(source_scaffold / "agents" / "agent.md")
    _write_markdown(source_scaffold / "hooks" / "hook.md")
    _write_markdown(source_scaffold / "skills" / "skill-a" / "SKILL.md")
    (source_scaffold / "teams" / "team-a").mkdir(parents=True)

    _write_markdown(legacy_scaffold / "agents" / "agent.md")
    _write_markdown(legacy_scaffold / "skills" / "skill-a" / "SKILL.md")

    monkeypatch.setattr(
        scaffold, "__file__", str(package_root / "utils" / "scaffold.py")
    )

    assert scaffold.get_scaffold_dir() == source_scaffold


def test_get_scaffold_dir_falls_back_to_legacy_bundle(monkeypatch, tmp_path: Path):
    package_root = tmp_path / "fleet_rlm"
    legacy_scaffold = package_root / "_scaffold"

    _write_markdown(legacy_scaffold / "agents" / "agent.md")
    _write_markdown(legacy_scaffold / "hooks" / "hook.md")
    _write_markdown(legacy_scaffold / "skills" / "skill-a" / "SKILL.md")
    (legacy_scaffold / "teams" / "team-a").mkdir(parents=True)

    monkeypatch.setattr(
        scaffold, "__file__", str(package_root / "utils" / "scaffold.py")
    )

    assert scaffold.get_scaffold_dir() == legacy_scaffold
