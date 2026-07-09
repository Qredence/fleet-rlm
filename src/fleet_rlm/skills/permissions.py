"""Skill permissions — visibility policy helpers (no RBAC yet)."""

from __future__ import annotations

from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillPermissionMode,
    SkillScope,
    SkillTrustLevel,
    SkillVisibilityPolicy,
)

_BUILTIN_SCOPES = frozenset({SkillScope.SCAFFOLD, SkillScope.SYSTEM})
_ADMIN_APPROVED_SCOPES = frozenset({SkillScope.ORG, SkillScope.PROJECT})


def default_permission_mode(scope: SkillScope) -> SkillPermissionMode:
    if scope in {SkillScope.SCAFFOLD, SkillScope.SYSTEM}:
        return SkillPermissionMode.READ_ONLY
    return SkillPermissionMode.READ_WRITE


def is_scope_visible(scope: SkillScope, policy: SkillVisibilityPolicy) -> bool:
    return scope in policy.visible_scopes


def is_skill_visible(name: str, scope: SkillScope, policy: SkillVisibilityPolicy) -> bool:
    if not is_scope_visible(scope, policy):
        return False
    if name in policy.excluded_skill_ids:
        return False
    if policy.included_skill_ids is not None and name not in policy.included_skill_ids:
        return False
    return True


def is_skill_script_execution_permitted(metadata: SkillMetadata) -> bool:
    """Return whether trusted script execution is allowed for *metadata*."""
    if metadata.trust_level != SkillTrustLevel.TRUSTED:
        return False
    return metadata.scope in _BUILTIN_SCOPES | _ADMIN_APPROVED_SCOPES


__all__ = [
    "default_permission_mode",
    "is_scope_visible",
    "is_skill_script_execution_permitted",
    "is_skill_visible",
]
