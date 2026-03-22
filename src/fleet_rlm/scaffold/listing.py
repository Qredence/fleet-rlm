"""Listing helpers for bundled scaffold assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._common import (
    get_scaffold_dir,
    is_ignored,
    iter_markdown_files,
    parse_frontmatter,
)


def list_skills() -> list[dict[str, Any]]:
    """List all available skills in the scaffold."""
    skills_dir = get_scaffold_dir() / "skills"
    skills: list[dict[str, Any]] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        frontmatter = parse_frontmatter(skill_md)
        file_count = sum(1 for file_path in skill_dir.rglob("*") if file_path.is_file())

        skills.append(
            {
                "name": skill_dir.name,
                "description": frontmatter.get("description", "No description"),
                "files": file_count,
            }
        )

    return skills


def list_agents() -> list[dict[str, Any]]:
    """List all available agents in the scaffold (including nested teams)."""
    agents_dir = get_scaffold_dir() / "agents"
    agents: list[dict[str, Any]] = []

    for agent_file in iter_markdown_files(agents_dir):
        frontmatter = parse_frontmatter(agent_file)
        rel = agent_file.relative_to(agents_dir)
        name = rel.with_suffix("").as_posix()
        agents.append(
            {
                "name": name,
                "description": frontmatter.get("description", "No description"),
                "model": frontmatter.get("model", "inherit"),
                "path": rel.as_posix(),
            }
        )

    return agents


def list_teams() -> list[dict[str, Any]]:
    """List all available team templates in the scaffold."""
    teams_dir = get_scaffold_dir() / "teams"
    if not teams_dir.exists():
        return []

    teams: list[dict[str, Any]] = []
    for team_dir in sorted(teams_dir.iterdir()):
        if not team_dir.is_dir() or is_ignored(Path(team_dir.name)):
            continue

        config_path = team_dir / "config.json"
        description = "No description"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                description = str(config.get("description", description))
            except json.JSONDecodeError:
                description = "Invalid team config JSON"

        file_count = sum(
            1
            for file_path in team_dir.rglob("*")
            if file_path.is_file() and not is_ignored(file_path.relative_to(team_dir))
        )
        teams.append(
            {
                "name": team_dir.name,
                "description": description,
                "files": file_count,
            }
        )

    return teams


def list_hooks() -> list[dict[str, Any]]:
    """List all available hook templates in the scaffold."""
    hooks_dir = get_scaffold_dir() / "hooks"
    if not hooks_dir.exists():
        return []

    hooks: list[dict[str, Any]] = []
    for hook_file in iter_markdown_files(hooks_dir):
        frontmatter = parse_frontmatter(hook_file)
        rel = hook_file.relative_to(hooks_dir)
        hooks.append(
            {
                "name": rel.as_posix(),
                "description": frontmatter.get("description")
                or frontmatter.get("name", "No description"),
                "event": frontmatter.get("event", ""),
            }
        )

    return hooks
