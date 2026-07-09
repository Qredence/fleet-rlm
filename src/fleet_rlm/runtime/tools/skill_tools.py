"""Backward-compatible re-exports for skill loading tools.

Canonical implementation: ``fleet_rlm.tools.skill_tools`` and ``fleet_rlm.skills.loader``.
"""

from __future__ import annotations

from fleet_rlm.skills.catalog import discover_scaffold_skills, iter_scaffold_skill_markdown
from fleet_rlm.skills.loader import _load_skill_impl, clear_skill_cache
from fleet_rlm.tools.skill_tools import (
    list_skills,
    list_skills_impl,
    load_skill,
    load_skill_tool_impl,
    read_skill_resource,
    read_skill_resource_impl,
    run_skill_script,
    run_skill_script_tool_impl,
)

__all__ = [
    "clear_skill_cache",
    "discover_scaffold_skills",
    "iter_scaffold_skill_markdown",
    "list_skills",
    "list_skills_impl",
    "load_skill",
    "load_skill_tool_impl",
    "read_skill_resource",
    "read_skill_resource_impl",
    "run_skill_script",
    "run_skill_script_tool_impl",
    "_load_skill_impl",
]
