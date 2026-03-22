from __future__ import annotations

from pathlib import Path

import fleet_rlm.scaffold as scaffold


def _write_markdown(path: Path, body: str = "# stub\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_get_scaffold_dir_uses_package_scaffold_directory(
    monkeypatch, tmp_path: Path
) -> None:
    package_root = tmp_path / "fleet_rlm"
    scaffold_dir = package_root / "scaffold"

    _write_markdown(scaffold_dir / "agents" / "agent.md")
    _write_markdown(scaffold_dir / "hooks" / "hook.md")
    _write_markdown(scaffold_dir / "skills" / "skill-a" / "SKILL.md")
    (scaffold_dir / "teams" / "team-a").mkdir(parents=True)

    monkeypatch.setattr(scaffold, "__file__", str(scaffold_dir / "__init__.py"))

    assert scaffold.get_scaffold_dir() == scaffold_dir
