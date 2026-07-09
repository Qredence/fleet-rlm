"""Local skill write primitives with staging, approval, and validation."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from fleet_rlm.skills.approval import approve_skill_change, create_staged_change, reject_skill_change
from fleet_rlm.skills.audit import record_audit_event
from fleet_rlm.skills.catalog import parse_skill_frontmatter, resolve_skill_metadata
from fleet_rlm.skills.errors import (
    SkillNotFoundError,
    SkillProtectedError,
    SkillValidationError,
    SkillWriteDeniedError,
)
from fleet_rlm.skills.loader import clear_skill_cache
from fleet_rlm.skills.permissions import is_scope_writable, is_skill_protected, requires_staging
from fleet_rlm.skills.schemas import (
    SkillApprovalStatus,
    SkillRuntimeContext,
    SkillScope,
    SkillWriteAction,
    SkillWriteContext,
    StagedSkillChange,
)
from fleet_rlm.skills.validator import (
    safe_skill_name,
    validate_skill_markdown,
    validate_write_target_path,
)

_SCOPE_PRECEDENCE_ORDER: tuple[SkillScope, ...] = (
    SkillScope.SESSION,
    SkillScope.USER,
    SkillScope.PROJECT,
    SkillScope.ORG,
    SkillScope.SYSTEM,
    SkillScope.SCAFFOLD,
)
_SCOPE_PRECEDENCE_INDEX = {scope: index for index, scope in enumerate(_SCOPE_PRECEDENCE_ORDER)}
_PROTECTED_SHADOW_SCOPES = frozenset({SkillScope.SCAFFOLD, SkillScope.SYSTEM, SkillScope.ORG, SkillScope.PROJECT})
_WRITABLE_SCOPES = frozenset({SkillScope.USER, SkillScope.SESSION})


def _content_hash(content: str | None) -> str | None:
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _runtime_context(write_context: SkillWriteContext) -> SkillRuntimeContext:
    return SkillRuntimeContext(volume_mount_path=write_context.volume_mount_path)


def _skills_root(context: SkillWriteContext) -> Path:
    return Path(context.volume_mount_path) / "skills"


def _scope_root(context: SkillWriteContext, scope: SkillScope) -> Path:
    return _skills_root(context) / scope.value


def _skill_directory(context: SkillWriteContext, scope: SkillScope, name: str) -> Path:
    return _scope_root(context, scope) / name


def _skill_markdown_path(context: SkillWriteContext, scope: SkillScope, name: str) -> Path:
    return _skill_directory(context, scope, name) / "SKILL.md"


def _source_label(scope: SkillScope) -> str:
    return scope.value


def _ensure_scope_writable(scope: SkillScope, context: SkillWriteContext) -> None:
    if not is_scope_writable(scope, context):
        raise SkillWriteDeniedError(f"Writes to scope '{scope.value}' are not permitted.")


def _resolve_existing_metadata(name: str, context: SkillWriteContext):
    return resolve_skill_metadata(name, _runtime_context(context))


def _validate_write_location(context: SkillWriteContext, scope: SkillScope, name: str) -> Path:
    normalized = safe_skill_name(name)
    scope_root = _scope_root(context, scope)
    target_dir = _skill_directory(context, scope, normalized)
    result = validate_write_target_path(scope_root, target_dir)
    if not result.valid:
        issue = result.issues[0]
        raise SkillValidationError(issue.message, code=issue.code)
    return target_dir


def _ensure_create_does_not_shadow_existing(
    name: str,
    context: SkillWriteContext,
    target_scope: SkillScope,
) -> None:
    """Block creates that would shadow built-in or higher-precedence skills."""
    normalized = safe_skill_name(name)
    if _skill_markdown_path(context, target_scope, normalized).is_file():
        return
    metadata = _resolve_existing_metadata(normalized, context)
    if metadata is None:
        return
    if metadata.scope in _PROTECTED_SHADOW_SCOPES:
        raise SkillProtectedError()
    existing_index = _SCOPE_PRECEDENCE_INDEX[metadata.scope]
    target_index = _SCOPE_PRECEDENCE_INDEX[target_scope]
    if existing_index <= target_index or target_index < existing_index:
        raise SkillProtectedError()


def _validate_markdown_for_write(raw_markdown: str, *, directory_name: str) -> str:
    result = validate_skill_markdown(raw_markdown, directory_name=directory_name)
    if not result.valid:
        issue = next(issue for issue in result.issues if issue.severity == "error")
        raise SkillValidationError(issue.message, code=issue.code)
    parsed_name, _ = parse_skill_frontmatter(raw_markdown)
    if not parsed_name:
        raise SkillValidationError("Skill name is required.", code="missing_name")
    if parsed_name != directory_name:
        raise SkillValidationError(
            f"Skill directory '{directory_name}' must match frontmatter name '{parsed_name}'.",
            code="directory_name_mismatch",
        )
    return parsed_name


def _read_existing_markdown(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def _commit_create_or_update(
    context: SkillWriteContext,
    scope: SkillScope,
    name: str,
    raw_markdown: str,
    *,
    action: SkillWriteAction,
    reason: str | None = None,
) -> None:
    _ensure_scope_writable(scope, context)
    target_dir = _validate_write_location(context, scope, name)
    normalized = _validate_markdown_for_write(raw_markdown, directory_name=name)
    if normalized != name:
        raise SkillValidationError(
            f"Skill directory '{name}' must match frontmatter name '{normalized}'.",
            code="directory_name_mismatch",
        )
    skill_md = target_dir / "SKILL.md"
    if action is SkillWriteAction.CREATE:
        if skill_md.is_file():
            raise SkillValidationError("Skill already exists.", code="skill_exists")
        _ensure_create_does_not_shadow_existing(name, context, scope)
    if action is SkillWriteAction.UPDATE and not skill_md.is_file():
        raise SkillNotFoundError(name)
    if action is SkillWriteAction.UPDATE:
        metadata = _resolve_existing_metadata(name, context)
        if metadata is not None and is_skill_protected(metadata) and metadata.scope is not scope:
            raise SkillProtectedError()
    old_hash = _content_hash(_read_existing_markdown(skill_md))
    _atomic_write_text(skill_md, raw_markdown)
    clear_skill_cache()
    record_audit_event(
        context=context,
        skill_name=normalized,
        scope=scope,
        action=action,
        source_label=_source_label(scope),
        old_content_hash=old_hash,
        new_content_hash=_content_hash(raw_markdown),
        approval_status=SkillApprovalStatus.APPROVED,
        reason=reason,
    )


def _commit_delete(context: SkillWriteContext, scope: SkillScope, name: str) -> None:
    _ensure_scope_writable(scope, context)
    target_dir = _validate_write_location(context, scope, name)
    skill_md = target_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillNotFoundError(name)
    metadata = _resolve_existing_metadata(name, context)
    if metadata is not None and is_skill_protected(metadata) and metadata.scope is not scope:
        raise SkillProtectedError()
    old_hash = _content_hash(skill_md.read_text(encoding="utf-8"))
    shutil.rmtree(target_dir)
    clear_skill_cache()
    record_audit_event(
        context=context,
        skill_name=name,
        scope=scope,
        action=SkillWriteAction.DELETE,
        source_label=_source_label(scope),
        old_content_hash=old_hash,
        new_content_hash=None,
        approval_status=SkillApprovalStatus.APPROVED,
    )


def stage_skill_change(
    *,
    scope: SkillScope,
    action: SkillWriteAction,
    name: str,
    raw_markdown: str | None,
    context: SkillWriteContext,
    reason: str | None = None,
) -> StagedSkillChange:
    """Create a staged skill change without mutating committed skill content."""
    _ensure_scope_writable(scope, context)
    normalized = safe_skill_name(name)
    _validate_write_location(context, scope, normalized)
    if action in {SkillWriteAction.CREATE, SkillWriteAction.UPDATE}:
        if raw_markdown is None:
            raise SkillValidationError("Skill markdown content is required.", code="missing_content")
        _validate_markdown_for_write(raw_markdown, directory_name=normalized)
        if action is SkillWriteAction.CREATE:
            existing = _skill_markdown_path(context, scope, normalized)
            if existing.is_file():
                raise SkillValidationError("Skill already exists.", code="skill_exists")
            _ensure_create_does_not_shadow_existing(normalized, context, scope)
        else:
            skill_md = _skill_markdown_path(context, scope, normalized)
            if not skill_md.is_file():
                raise SkillNotFoundError(normalized)
            metadata = _resolve_existing_metadata(normalized, context)
            if metadata is not None and is_skill_protected(metadata) and metadata.scope is not scope:
                raise SkillProtectedError()
    elif action is SkillWriteAction.DELETE:
        skill_md = _skill_markdown_path(context, scope, normalized)
        if not skill_md.is_file():
            raise SkillNotFoundError(normalized)
        metadata = _resolve_existing_metadata(normalized, context)
        if metadata is not None and is_skill_protected(metadata) and metadata.scope is not scope:
            raise SkillProtectedError()
    else:
        raise SkillValidationError("Unsupported staged action.", code="invalid_staged_action")

    old_markdown = _read_existing_markdown(_skill_markdown_path(context, scope, normalized))
    return create_staged_change(
        context=context,
        skill_name=normalized,
        scope=scope,
        action=action,
        raw_markdown=raw_markdown,
        old_content_hash=_content_hash(old_markdown),
        new_content_hash=_content_hash(raw_markdown),
        source_label=_source_label(scope),
        reason=reason,
    )


def _maybe_stage_or_commit(
    *,
    scope: SkillScope,
    action: SkillWriteAction,
    name: str,
    raw_markdown: str | None,
    context: SkillWriteContext,
    commit_fn: Callable[[], None],
    reason: str | None = None,
) -> StagedSkillChange | None:
    if requires_staging(context):
        return stage_skill_change(
            scope=scope,
            action=action,
            name=name,
            raw_markdown=raw_markdown,
            context=context,
            reason=reason,
        )
    commit_fn()
    return None


def _approve_commit(change: StagedSkillChange, markdown: str | None, context: SkillWriteContext) -> None:
    if change.action is SkillWriteAction.CREATE:
        _commit_create_or_update(
            context,
            change.scope,
            change.skill_name,
            markdown or "",
            action=SkillWriteAction.CREATE,
            reason=change.reason,
        )
    elif change.action is SkillWriteAction.UPDATE:
        _commit_create_or_update(
            context,
            change.scope,
            change.skill_name,
            markdown or "",
            action=SkillWriteAction.UPDATE,
            reason=change.reason,
        )
    elif change.action is SkillWriteAction.DELETE:
        _commit_delete(context, change.scope, change.skill_name)


def approve_staged_skill_change(staged_id: str, context: SkillWriteContext) -> StagedSkillChange:
    return approve_skill_change(
        staged_id, context, commit_fn=lambda change, markdown: _approve_commit(change, markdown, context)
    )


def reject_staged_skill_change(
    staged_id: str,
    context: SkillWriteContext,
    *,
    reason: str | None = None,
) -> StagedSkillChange:
    return reject_skill_change(staged_id, context, reason=reason)


def write_skill_for_scope(
    *,
    scope: SkillScope,
    action: SkillWriteAction,
    name: str,
    raw_markdown: str | None,
    context: SkillWriteContext,
    reason: str | None = None,
) -> StagedSkillChange | None:
    """Dispatch create/update/delete for supported writable scopes."""
    if scope not in _WRITABLE_SCOPES:
        raise SkillWriteDeniedError(f"Writes to scope '{scope.value}' are not permitted.")
    if action in {SkillWriteAction.CREATE, SkillWriteAction.UPDATE} and raw_markdown is None:
        raise SkillValidationError("Skill markdown content is required.", code="missing_content")

    normalized = safe_skill_name(name)

    def commit_fn() -> None:
        if action in {SkillWriteAction.CREATE, SkillWriteAction.UPDATE}:
            _commit_create_or_update(
                context,
                scope,
                normalized,
                raw_markdown or "",
                action=action,
                reason=reason,
            )
            return
        if action is SkillWriteAction.DELETE:
            _commit_delete(context, scope, normalized)
            return
        raise SkillValidationError("Unsupported write action.", code="invalid_write_action")

    return _maybe_stage_or_commit(
        scope=scope,
        action=action,
        name=name,
        raw_markdown=raw_markdown,
        context=context,
        commit_fn=commit_fn,
        reason=reason,
    )


__all__ = [
    "approve_staged_skill_change",
    "reject_staged_skill_change",
    "stage_skill_change",
    "write_skill_for_scope",
]
