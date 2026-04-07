from __future__ import annotations

import json
from pathlib import Path

import fleet_rlm.scaffold as scaffold
from fleet_rlm.scaffold.installers import install_all


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


def test_install_all_preserves_installable_scaffold_asset_classes(
    monkeypatch, tmp_path: Path
) -> None:
    package_root = tmp_path / "fleet_rlm"
    scaffold_dir = package_root / "scaffold"

    _write_markdown(
        scaffold_dir / "agents" / "agent.md",
        "---\ndescription: Agent\nmodel: inherit\n---\n",
    )
    _write_markdown(
        scaffold_dir / "hooks" / "hook.md",
        "---\ndescription: Hook\nevent: prompt\n---\n",
    )
    _write_markdown(
        scaffold_dir / "skills" / "skill-a" / "SKILL.md",
        "---\ndescription: Skill A\n---\n",
    )
    _write_markdown(
        scaffold_dir / "skills" / "skill-a" / "references" / "guide.md",
        "# guide\n",
    )
    (scaffold_dir / "skills" / "skill-a" / "scripts").mkdir(parents=True)
    (scaffold_dir / "skills" / "skill-a" / "scripts" / "tool.py").write_text(
        "print('tool')\n", encoding="utf-8"
    )
    (scaffold_dir / "teams" / "team-a" / "inboxes").mkdir(parents=True)
    (scaffold_dir / "teams" / "team-a" / "config.json").write_text(
        json.dumps({"description": "Team A"}), encoding="utf-8"
    )
    (scaffold_dir / "teams" / "team-a" / "inboxes" / "agent.json").write_text(
        json.dumps({"description": "Inbox"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        "fleet_rlm.scaffold.installers.get_scaffold_dir", lambda: scaffold_dir
    )
    monkeypatch.setattr(
        "fleet_rlm.scaffold.listing.get_scaffold_dir", lambda: scaffold_dir
    )

    target = tmp_path / "claude"
    result = install_all(target)

    assert result == {
        "skills_installed": ["skill-a"],
        "agents_installed": ["agent"],
        "teams_installed": ["team-a"],
        "hooks_installed": ["hook.md"],
        "skills_total": 1,
        "agents_total": 1,
        "teams_total": 1,
        "hooks_total": 1,
    }
    assert (
        (target / "agents" / "agent.md").read_text(encoding="utf-8").startswith("---")
    )
    assert (target / "hooks" / "hook.md").read_text(encoding="utf-8").startswith("---")
    assert (target / "skills" / "skill-a" / "references" / "guide.md").read_text(
        encoding="utf-8"
    ) == "# guide\n"
    assert (target / "skills" / "skill-a" / "scripts" / "tool.py").read_text(
        encoding="utf-8"
    ) == "print('tool')\n"
    assert json.loads(
        (target / "teams" / "team-a" / "config.json").read_text(encoding="utf-8")
    ) == {"description": "Team A"}
    assert json.loads(
        (target / "teams" / "team-a" / "inboxes" / "agent.json").read_text(
            encoding="utf-8"
        )
    ) == {"description": "Inbox"}
