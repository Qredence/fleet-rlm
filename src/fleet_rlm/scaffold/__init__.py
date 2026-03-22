"""Bootstrap Claude Code scaffold assets to user-level directories.

This package exposes a thin facade over scaffold discovery, listing, and
installation helpers used by ``fleet-rlm init``.
"""

from __future__ import annotations

from pathlib import Path

from ._common import get_scaffold_dir as _get_scaffold_dir
from .installers import (
    install_agents,
    install_all,
    install_hooks,
    install_skills,
    install_teams,
)
from .listing import list_agents, list_hooks, list_skills, list_teams


def get_scaffold_dir() -> Path:
    """Return the bundled scaffold root using the facade module path."""
    return _get_scaffold_dir(package_file=__file__)


__all__ = [
    "get_scaffold_dir",
    "install_agents",
    "install_all",
    "install_hooks",
    "install_skills",
    "install_teams",
    "list_agents",
    "list_hooks",
    "list_skills",
    "list_teams",
]
