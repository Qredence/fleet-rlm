"""Shared scaffold asset discovery and metadata helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def is_ignored(path: Path) -> bool:
    """Return True if a path component should be ignored."""
    return any(
        part.startswith(".") or part in {".DS_Store", "__pycache__"}
        for part in path.parts
    )


def has_visible_entries(path: Path, *, directories_only: bool = False) -> bool:
    """Return True when *path* contains at least one non-ignored entry."""
    if not path.exists():
        return False

    for child in path.iterdir():
        if is_ignored(Path(child.name)):
            continue
        if directories_only and not child.is_dir():
            continue
        return True

    return False


def is_complete_scaffold_dir(scaffold_dir: Path) -> bool:
    """Return True when a scaffold tree contains all installable categories."""
    return (
        has_visible_entries(scaffold_dir / "skills", directories_only=True)
        and has_visible_entries(scaffold_dir / "agents")
        and has_visible_entries(scaffold_dir / "teams", directories_only=True)
        and has_visible_entries(scaffold_dir / "hooks")
    )


def get_scaffold_dir(*, package_file: str | Path | None = None) -> Path:
    """Return the path to the bundled scaffold asset tree."""
    file_ref = Path(package_file) if package_file is not None else Path(__file__)
    scaffold_dir = file_ref.resolve().parent
    if is_complete_scaffold_dir(scaffold_dir):
        return scaffold_dir
    raise FileNotFoundError(
        "Scaffold directory is incomplete or missing required asset categories: "
        f"{scaffold_dir}"
    )


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Parse simple YAML frontmatter from a markdown file."""
    content = path.read_text()
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    frontmatter: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter


def iter_markdown_files(root: Path) -> list[Path]:
    """Return non-hidden markdown files under root, recursively."""
    return sorted(
        p
        for p in root.rglob("*.md")
        if p.is_file() and not is_ignored(p.relative_to(root))
    )
