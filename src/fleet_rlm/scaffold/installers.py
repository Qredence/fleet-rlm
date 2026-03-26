"""Installation helpers for bundled scaffold assets."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ._common import get_scaffold_dir, is_ignored, iter_markdown_files
from .listing import list_agents, list_hooks, list_skills, list_teams


def install_skills(target: Path, *, force: bool = False) -> list[str]:
    """Install skills to the target directory."""
    source_skills = get_scaffold_dir() / "skills"
    target_skills = target / "skills"
    target_skills.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for skill_dir in sorted(source_skills.iterdir()):
        if not skill_dir.is_dir():
            continue

        target_skill = target_skills / skill_dir.name
        if target_skill.exists() and not force:
            continue
        if target_skill.exists():
            shutil.rmtree(target_skill)
        shutil.copytree(
            skill_dir,
            target_skill,
            ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"),
        )
        installed.append(skill_dir.name)

    return installed


def install_agents(target: Path, *, force: bool = False) -> list[str]:
    """Install agents (including nested team agents) to the target directory."""
    source_agents = get_scaffold_dir() / "agents"
    target_agents = target / "agents"
    target_agents.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for agent_file in iter_markdown_files(source_agents):
        relative_path = agent_file.relative_to(source_agents)
        target_file = target_agents / relative_path
        if target_file.exists() and not force:
            continue

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_file, target_file)
        installed.append(relative_path.with_suffix("").as_posix())

    return installed


def install_teams(target: Path, *, force: bool = False) -> list[str]:
    """Install team templates to the target directory."""
    source_teams = get_scaffold_dir() / "teams"
    if not source_teams.exists():
        return []

    target_teams = target / "teams"
    target_teams.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for team_dir in sorted(source_teams.iterdir()):
        if not team_dir.is_dir() or is_ignored(Path(team_dir.name)):
            continue

        target_team = target_teams / team_dir.name
        if target_team.exists() and not force:
            continue
        if target_team.exists():
            shutil.rmtree(target_team)
        shutil.copytree(
            team_dir,
            target_team,
            ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"),
        )
        installed.append(team_dir.name)

    return installed


def install_hooks(target: Path, *, force: bool = False) -> list[str]:
    """Install hooks to the target directory."""
    source_hooks = get_scaffold_dir() / "hooks"
    if not source_hooks.exists():
        return []

    target_hooks = target / "hooks"
    target_hooks.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for source_file in sorted(source_hooks.rglob("*")):
        if not source_file.is_file():
            continue
        relative_path = source_file.relative_to(source_hooks)
        if is_ignored(relative_path):
            continue

        target_file = target_hooks / relative_path
        if target_file.exists() and not force:
            continue

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        installed.append(relative_path.as_posix())

    return installed


def install_all(
    target: Path,
    *,
    force: bool = False,
    include_teams: bool = True,
    include_hooks: bool = True,
) -> dict[str, Any]:
    """Install scaffold assets to the target directory."""
    skills_installed = install_skills(target, force=force)
    agents_installed = install_agents(target, force=force)
    teams_installed = install_teams(target, force=force) if include_teams else []
    hooks_installed = install_hooks(target, force=force) if include_hooks else []

    return {
        "skills_installed": skills_installed,
        "agents_installed": agents_installed,
        "teams_installed": teams_installed,
        "hooks_installed": hooks_installed,
        "skills_total": len(list_skills()),
        "agents_total": len(list_agents()),
        "teams_total": len(list_teams()),
        "hooks_total": len(list_hooks()),
    }
