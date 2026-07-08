"""Backward-compatible re-exports for skill loading tools.

Canonical implementation: ``fleet_rlm.skills.loader`` and ``fleet_rlm.skills.catalog``.
"""

from __future__ import annotations

from fleet_rlm.skills.catalog import discover_scaffold_skills, iter_scaffold_skill_markdown
from fleet_rlm.skills.loader import _load_skill_impl, clear_skill_cache, load_skill

__all__ = [
    "clear_skill_cache",
    "discover_scaffold_skills",
    "iter_scaffold_skill_markdown",
    "load_skill",
    "_load_skill_impl",
]
