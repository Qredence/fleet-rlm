"""Fleet Skills runtime package — catalog, loader, selection, and REPL injection."""

from __future__ import annotations

import importlib
from typing import Any

from fleet_rlm.skills.active import ActiveSkills
from fleet_rlm.skills.catalog import (
    clear_catalog_cache,
    discover_scaffold_skills,
    inventory_skill_resources,
    iter_all_skill_metadata,
    iter_scaffold_skill_markdown,
    resolve_skill_metadata,
)
from fleet_rlm.skills.loader import (
    clear_skill_cache,
    load_resource,
    load_skill_bundle,
    load_skill_impl,
)
from fleet_rlm.skills.permissions import (
    default_permission_mode,
    is_scope_visible,
    is_skill_visible,
)
from fleet_rlm.skills.repository import AVAILABLE_SKILLS, get_skill_description, list_skill_names, list_visible
from fleet_rlm.skills.schemas import (
    LoadSkillInput,
    LoadSkillOutput,
    SkillBundle,
    SkillCatalogEntry,
    SkillMetadata,
    SkillPermissionMode,
    SkillResource,
    SkillResourceKind,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
    SkillValidationIssue,
    SkillValidationResult,
    SkillVisibilityPolicy,
)
from fleet_rlm.skills.sync import seed_system_skills
from fleet_rlm.skills.validator import (
    safe_skill_name,
    validate_resource_path,
    validate_skill_bundle,
    validate_skill_metadata,
)

# Backward-compatible alias for modules that imported private loader symbol.
_load_skill_impl = load_skill_impl

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "SkillSelectionModule": ("fleet_rlm.skills.selection", "SkillSelectionModule"),
    "load_skill": ("fleet_rlm.tools.skill_tools", "load_skill"),
    "preview_skills_for_turn": ("fleet_rlm.skills.selection", "preview_skills_for_turn"),
    "select_skill_candidates": ("fleet_rlm.skills.selection", "select_skill_candidates"),
    "SkillSelectionSignature": ("fleet_rlm.skills.signatures", "SkillSelectionSignature"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_name, attr_name = _LAZY_EXPORTS[name]
        return getattr(importlib.import_module(module_name), attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS) | set(__all__))


__all__ = [
    "AVAILABLE_SKILLS",
    "ActiveSkills",
    "LoadSkillInput",
    "LoadSkillOutput",
    "SkillBundle",
    "SkillCatalogEntry",
    "SkillMetadata",
    "SkillPermissionMode",
    "SkillResource",
    "SkillResourceKind",
    "SkillRuntimeContext",
    "SkillScope",
    "SkillSelectionModule",
    "SkillSelectionSignature",
    "SkillTrustLevel",
    "SkillValidationIssue",
    "SkillValidationResult",
    "SkillVisibilityPolicy",
    "_load_skill_impl",
    "clear_catalog_cache",
    "clear_skill_cache",
    "default_permission_mode",
    "discover_scaffold_skills",
    "get_skill_description",
    "inventory_skill_resources",
    "is_scope_visible",
    "is_skill_visible",
    "iter_all_skill_metadata",
    "iter_scaffold_skill_markdown",
    "list_skill_names",
    "list_visible",
    "load_resource",
    "load_skill",
    "load_skill_bundle",
    "load_skill_impl",
    "preview_skills_for_turn",
    "resolve_skill_metadata",
    "safe_skill_name",
    "seed_system_skills",
    "select_skill_candidates",
    "validate_resource_path",
    "validate_skill_bundle",
    "validate_skill_metadata",
]
