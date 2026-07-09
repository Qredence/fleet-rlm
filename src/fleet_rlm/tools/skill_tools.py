"""Read-only RLM skill tools backed by the Fleet Skills package."""

from __future__ import annotations

from typing import Any

from fleet_rlm.runtime.tools._marker import tool_fn
from fleet_rlm.skills.loader import default_skill_runtime_context
from fleet_rlm.skills.schemas import LoadSkillInput, SkillRuntimeContext
from fleet_rlm.skills.service import (
    list_skills_output,
    load_skill_public_output,
    read_skill_resource_public_output,
)


def list_skills_impl(*, context: SkillRuntimeContext | None = None) -> dict[str, Any]:
    """Return visible skill metadata only."""
    ctx = context or default_skill_runtime_context()
    return list_skills_output(context=ctx).model_dump()


def load_skill_tool_impl(name: str, *, context: SkillRuntimeContext | None = None) -> dict[str, Any]:
    """Load one visible skill bundle with instructions and resource inventory."""
    ctx = context or default_skill_runtime_context()
    return load_skill_public_output(name, context=ctx).model_dump()


def read_skill_resource_impl(
    name: str,
    resource_path: str,
    *,
    context: SkillRuntimeContext | None = None,
) -> dict[str, Any]:
    """Read one safe resource body from a visible skill."""
    ctx = context or default_skill_runtime_context()
    return read_skill_resource_public_output(name, resource_path, context=ctx).model_dump()


@tool_fn
def list_skills() -> dict[str, Any]:
    """List visible skill metadata from the catalog."""
    return list_skills_impl()


@tool_fn
def load_skill(name: str) -> dict[str, Any]:
    """Load a human-curated markdown skill from the persistent volume."""
    validated = LoadSkillInput(name=name)
    return load_skill_tool_impl(validated.name)


@tool_fn
def read_skill_resource(name: str, resource_path: str) -> dict[str, Any]:
    """Read one safe resource file from a visible skill bundle."""
    return read_skill_resource_impl(name, resource_path)


__all__ = [
    "list_skills",
    "list_skills_impl",
    "load_skill",
    "load_skill_tool_impl",
    "read_skill_resource",
    "read_skill_resource_impl",
]
