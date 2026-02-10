"""Bootstrap Claude Code scaffold assets to user-level directory.

This module provides functions to copy the bundled RLM skills, agents, teams,
and hooks
from the installed fleet-rlm package to the user's ~/.claude/ directory,
making them available across all projects.

The scaffold data is bundled in the PyPI package under _scaffold/ and
includes:
    - 10 skills in _scaffold/skills/
    - Agent definitions in _scaffold/agents/ (including nested teams/)
    - Team templates in _scaffold/teams/
    - Hook rules in _scaffold/hooks/

Usage:
    from fleet_rlm.scaffold import install_all
    install_all(Path.home() / ".claude")

Or via CLI:
    $ fleet-rlm init
    $ fleet-rlm init --force  # overwrite existing
    $ fleet-rlm init --list   # show available
"""

from __future__ import annotations

import re
import shutil
import json
from pathlib import Path
from typing import Any


def get_scaffold_dir() -> Path:
    """Get the path to the bundled scaffold directory.

    Returns:
        Path to the _scaffold/ directory in the installed package.

    Raises:
        FileNotFoundError: If the scaffold directory cannot be located.
    """
    # The _scaffold directory is adjacent to this file
    scaffold_dir = Path(__file__).parent / "_scaffold"
    if not scaffold_dir.exists():
        raise FileNotFoundError(
            f"Scaffold directory not found at {scaffold_dir}. "
            "The fleet-rlm package may not be properly installed."
        )
    return scaffold_dir


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file.

    Args:
        path: Path to the markdown file.

    Returns:
        Dictionary of frontmatter fields. Returns empty dict if no
        frontmatter found.
    """
    content = path.read_text()
    # Match YAML frontmatter: --- at start, --- at end
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    # Simple YAML parsing (just key: value pairs, no nesting)
    frontmatter = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter


def _is_ignored(path: Path) -> bool:
    """Return True if a path component should be ignored."""
    return any(part.startswith(".") or part == ".DS_Store" for part in path.parts)


def _iter_markdown_files(root: Path) -> list[Path]:
    """Return non-hidden markdown files under root, recursively."""
    return sorted(
        p
        for p in root.rglob("*.md")
        if p.is_file() and not _is_ignored(p.relative_to(root))
    )


def list_skills() -> list[dict[str, Any]]:
    """List all available skills in the scaffold.

    Returns:
        List of skill metadata dictionaries with keys:
            - name: Skill directory name
            - description: Skill description from frontmatter
            - files: Number of files in the skill directory
    """
    scaffold = get_scaffold_dir()
    skills_dir = scaffold / "skills"
    skills = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        frontmatter = _parse_frontmatter(skill_md)
        file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())

        skills.append(
            {
                "name": skill_dir.name,
                "description": frontmatter.get("description", "No description"),
                "files": file_count,
            }
        )

    return skills


def list_agents() -> list[dict[str, Any]]:
    """List all available agents in the scaffold (including nested teams).

    Returns:
        List of agent metadata dictionaries with keys:
            - name: Agent path/name (without .md extension)
            - description: Agent description from frontmatter
            - model: Model specified in frontmatter
    """
    scaffold = get_scaffold_dir()
    agents_dir = scaffold / "agents"
    agents = []

    for agent_file in _iter_markdown_files(agents_dir):
        frontmatter = _parse_frontmatter(agent_file)
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
    scaffold = get_scaffold_dir()
    teams_dir = scaffold / "teams"
    if not teams_dir.exists():
        return []

    teams = []
    for team_dir in sorted(teams_dir.iterdir()):
        if not team_dir.is_dir() or _is_ignored(Path(team_dir.name)):
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
            if file_path.is_file() and not _is_ignored(file_path.relative_to(team_dir))
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
    scaffold = get_scaffold_dir()
    hooks_dir = scaffold / "hooks"
    if not hooks_dir.exists():
        return []

    hooks = []
    for hook_file in _iter_markdown_files(hooks_dir):
        frontmatter = _parse_frontmatter(hook_file)
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


def install_skills(target: Path, *, force: bool = False) -> list[str]:
    """Install skills to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude). Skills installed to
            target/skills/.
        force: If True, overwrite existing files. If False, skip existing.

    Returns:
        List of skill names that were installed.

    Raises:
        FileNotFoundError: If the scaffold directory is not found.
    """
    scaffold = get_scaffold_dir()
    source_skills = scaffold / "skills"
    target_skills = target / "skills"
    target_skills.mkdir(parents=True, exist_ok=True)

    installed = []

    for skill_dir in sorted(source_skills.iterdir()):
        if not skill_dir.is_dir():
            continue

        target_skill = target_skills / skill_dir.name

        if target_skill.exists() and not force:
            continue  # Skip existing

        # Copy the entire skill directory
        if target_skill.exists():
            shutil.rmtree(target_skill)
        shutil.copytree(skill_dir, target_skill)
        installed.append(skill_dir.name)

    return installed


def install_agents(target: Path, *, force: bool = False) -> list[str]:
    """Install agents (including nested team agents) to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude). Agents installed to
            target/agents/.
        force: If True, overwrite existing files. If False, skip existing.

    Returns:
        List of agent names that were installed.

    Raises:
        FileNotFoundError: If the scaffold directory is not found.
    """
    scaffold = get_scaffold_dir()
    source_agents = scaffold / "agents"
    target_agents = target / "agents"
    target_agents.mkdir(parents=True, exist_ok=True)

    installed = []

    for agent_file in _iter_markdown_files(source_agents):
        relative_path = agent_file.relative_to(source_agents)
        target_file = target_agents / relative_path

        if target_file.exists() and not force:
            continue  # Skip existing

        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(agent_file, target_file)
        installed.append(relative_path.with_suffix("").as_posix())

    return installed


def install_teams(target: Path, *, force: bool = False) -> list[str]:
    """Install team templates to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude). Teams installed to
            target/teams/.
        force: If True, overwrite existing teams. If False, skip existing.

    Returns:
        List of team names that were installed.
    """
    scaffold = get_scaffold_dir()
    source_teams = scaffold / "teams"
    if not source_teams.exists():
        return []

    target_teams = target / "teams"
    target_teams.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for team_dir in sorted(source_teams.iterdir()):
        if not team_dir.is_dir() or _is_ignored(Path(team_dir.name)):
            continue

        target_team = target_teams / team_dir.name
        if target_team.exists() and not force:
            continue

        if target_team.exists():
            shutil.rmtree(target_team)
        shutil.copytree(
            team_dir, target_team, ignore=shutil.ignore_patterns(".DS_Store")
        )
        installed.append(team_dir.name)

    return installed


def install_hooks(target: Path, *, force: bool = False) -> list[str]:
    """Install hooks to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude). Hooks installed to
            target/hooks/.
        force: If True, overwrite existing files. If False, skip existing.

    Returns:
        List of hook paths that were installed.
    """
    scaffold = get_scaffold_dir()
    source_hooks = scaffold / "hooks"
    if not source_hooks.exists():
        return []

    target_hooks = target / "hooks"
    target_hooks.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for source_file in sorted(source_hooks.rglob("*")):
        if not source_file.is_file():
            continue
        relative_path = source_file.relative_to(source_hooks)
        if _is_ignored(relative_path):
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
    """Install scaffold assets to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude).
        force: If True, overwrite existing files. If False, skip existing.
        include_teams: Whether to install team templates.
        include_hooks: Whether to install hook templates.

    Returns:
        Summary dictionary with keys:
            - skills_installed: List of skill names installed
            - agents_installed: List of agent names installed
            - teams_installed: List of team names installed
            - hooks_installed: List of hooks installed
            - skills_total: Total available skills
            - agents_total: Total available agents
            - teams_total: Total available teams
            - hooks_total: Total available hooks
    """
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
