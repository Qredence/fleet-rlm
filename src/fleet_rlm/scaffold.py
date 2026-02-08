"""Bootstrap Claude Code skills and agents to user-level directory.

This module provides functions to copy the bundled RLM skills and agents
from the installed fleet-rlm package to the user's ~/.claude/ directory,
making them available across all projects.

The scaffold data is bundled in the PyPI package under _scaffold/ and
includes:
    - 10 skills in _scaffold/skills/
    - 4 agents in _scaffold/agents/

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
    """List all available agents in the scaffold.

    Returns:
        List of agent metadata dictionaries with keys:
            - name: Agent name (without .md extension)
            - description: Agent description from frontmatter
            - model: Model specified in frontmatter
    """
    scaffold = get_scaffold_dir()
    agents_dir = scaffold / "agents"
    agents = []

    for agent_file in sorted(agents_dir.glob("*.md")):
        frontmatter = _parse_frontmatter(agent_file)

        agents.append(
            {
                "name": agent_file.stem,
                "description": frontmatter.get("description", "No description"),
                "model": frontmatter.get("model", "inherit"),
            }
        )

    return agents


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
    """Install agents to the target directory.

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

    for agent_file in sorted(source_agents.glob("*.md")):
        target_file = target_agents / agent_file.name

        if target_file.exists() and not force:
            continue  # Skip existing

        shutil.copy2(agent_file, target_file)
        installed.append(agent_file.stem)

    return installed


def install_all(target: Path, *, force: bool = False) -> dict[str, Any]:
    """Install both skills and agents to the target directory.

    Args:
        target: Base directory (e.g., ~/.claude).
        force: If True, overwrite existing files. If False, skip existing.

    Returns:
        Summary dictionary with keys:
            - skills_installed: List of skill names installed
            - agents_installed: List of agent names installed
            - skills_total: Total available skills
            - agents_total: Total available agents
    """
    skills_installed = install_skills(target, force=force)
    agents_installed = install_agents(target, force=force)

    return {
        "skills_installed": skills_installed,
        "agents_installed": agents_installed,
        "skills_total": len(list_skills()),
        "agents_total": len(list_agents()),
    }
