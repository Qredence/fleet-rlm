"""Skill permissions — visibility policy helpers (no RBAC yet)."""

from __future__ import annotations

from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillPermissionMode,
    SkillScope,
    SkillTrustLevel,
    SkillVisibilityPolicy,
    SkillWriteContext,
)

_BUILTIN_SCOPES = frozenset({SkillScope.SCAFFOLD, SkillScope.SYSTEM})
_ADMIN_APPROVED_SCOPES = frozenset({SkillScope.ORG, SkillScope.PROJECT})
_DEFAULT_DENIED_WRITE_SCOPES = frozenset({SkillScope.SCAFFOLD, SkillScope.SYSTEM, SkillScope.ORG, SkillScope.PROJECT})


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


def is_scope_writable(scope: SkillScope, context: SkillWriteContext) -> bool:
    """Return whether *scope* is writable under the provided write context."""
    if scope in context.admin_writable_scopes:
        return True
    if scope in _DEFAULT_DENIED_WRITE_SCOPES:
        return False
    if scope is SkillScope.USER:
        return context.policy.user_writes_enabled
    if scope is SkillScope.SESSION:
        return context.policy.session_writes_enabled
    return False


def is_skill_protected(metadata: SkillMetadata) -> bool:
    """Return whether an existing skill metadata record is protected from writes."""
    if metadata.scope in _DEFAULT_DENIED_WRITE_SCOPES:
        return True
    return metadata.permission_mode is SkillPermissionMode.READ_ONLY


def requires_staging(context: SkillWriteContext) -> bool:
    """Return whether writes must be staged before commit."""
    if context.policy.require_staging:
        return True
    return context.actor == "agent" and context.policy.agent_writes_require_staging


__all__ = [
    "default_permission_mode",
    "is_scope_visible",
    "is_scope_writable",
    "is_skill_protected",
    "is_skill_script_execution_permitted",
    "is_skill_visible",
    "requires_staging",
]
