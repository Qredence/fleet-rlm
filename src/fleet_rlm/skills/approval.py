"""File-backed staging and approval workflow for skill writes."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fleet_rlm.skills.audit import record_audit_event
from fleet_rlm.skills.errors import SkillValidationError, StagedChangeNotFoundError
from fleet_rlm.skills.schemas import (
    SkillApprovalStatus,
    SkillScope,
    SkillWriteAction,
    SkillWriteContext,
    StagedSkillChange,
)

STAGING_DIR_NAME = ".staging"
MANIFEST_FILENAME = "manifest.json"
SKILL_FILENAME = "SKILL.md"


def staging_root(volume_mount_path: str) -> Path:
    """Bounded file-backed staging area under the approved skill volume root."""
    return Path(volume_mount_path) / "skills" / STAGING_DIR_NAME


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _staged_dir(staging_id: str, volume_mount_path: str) -> Path:
    return staging_root(volume_mount_path) / staging_id


def _write_manifest(path: Path, change: StagedSkillChange) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(change.model_dump_json(indent=2), encoding="utf-8")


def _load_manifest(path: Path) -> StagedSkillChange:
    return StagedSkillChange.model_validate_json(path.read_text(encoding="utf-8"))


def create_staged_change(
    *,
    context: SkillWriteContext,
    skill_name: str,
    scope: SkillScope,
    action: SkillWriteAction,
    raw_markdown: str | None,
    old_content_hash: str | None,
    new_content_hash: str | None,
    source_label: str,
    reason: str | None = None,
) -> StagedSkillChange:
    staged_id = uuid.uuid4().hex
    change = StagedSkillChange(
        id=staged_id,
        skill_name=skill_name,
        scope=scope,
        action=action,
        status=SkillApprovalStatus.PENDING,
        created_at=_utc_now_iso(),
        raw_markdown=raw_markdown,
        actor=context.actor,
        user_id=context.user_id,
        session_id=context.session_id,
        workspace_id=context.workspace_id,
        org_id=context.org_id,
        old_content_hash=old_content_hash,
        new_content_hash=new_content_hash,
        source_label=source_label,
        reason=reason,
    )
    staged_dir = _staged_dir(staged_id, context.volume_mount_path)
    staged_dir.mkdir(parents=True, exist_ok=False)
    _write_manifest(staged_dir / MANIFEST_FILENAME, change)
    if raw_markdown is not None:
        (staged_dir / SKILL_FILENAME).write_text(raw_markdown, encoding="utf-8")
    record_audit_event(
        context=context,
        skill_name=skill_name,
        scope=scope,
        action=SkillWriteAction.STAGE,
        source_label=source_label,
        old_content_hash=old_content_hash,
        new_content_hash=new_content_hash,
        approval_status=SkillApprovalStatus.PENDING,
        reason=reason,
        staged_change_id=staged_id,
    )
    return change


def _assert_staged_change_owned(change: StagedSkillChange, context: SkillWriteContext) -> None:
    """Reject cross-tenant or cross-user approval attempts with a sanitized not-found."""
    if change.workspace_id and context.workspace_id and change.workspace_id != context.workspace_id:
        raise StagedChangeNotFoundError()
    if change.user_id and context.user_id and change.user_id != context.user_id:
        raise StagedChangeNotFoundError()


def get_staged_change(staged_id: str, context: SkillWriteContext) -> StagedSkillChange:
    manifest_path = _staged_dir(staged_id, context.volume_mount_path) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise StagedChangeNotFoundError()
    change = _load_manifest(manifest_path)
    if change.status is not SkillApprovalStatus.PENDING:
        raise StagedChangeNotFoundError()
    return change


def list_pending_staged_changes(context: SkillWriteContext) -> list[StagedSkillChange]:
    root = staging_root(context.volume_mount_path)
    if not root.is_dir():
        return []
    pending: list[StagedSkillChange] = []
    for entry in sorted(root.iterdir()):
        manifest_path = entry / MANIFEST_FILENAME
        if not manifest_path.is_file():
            continue
        change = _load_manifest(manifest_path)
        if change.status is SkillApprovalStatus.PENDING:
            pending.append(change)
    return pending


def mark_staged_change(
    staged_id: str,
    context: SkillWriteContext,
    *,
    status: SkillApprovalStatus,
    reason: str | None = None,
) -> StagedSkillChange:
    staged_dir = _staged_dir(staged_id, context.volume_mount_path)
    manifest_path = staged_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise StagedChangeNotFoundError()
    change = _load_manifest(manifest_path)
    if change.status is not SkillApprovalStatus.PENDING:
        raise StagedChangeNotFoundError()
    updated = change.model_copy(update={"status": status, "reason": reason or change.reason})
    _write_manifest(manifest_path, updated)
    return updated


def remove_staged_change(staged_id: str, context: SkillWriteContext) -> None:
    staged_dir = _staged_dir(staged_id, context.volume_mount_path)
    if staged_dir.is_dir():
        shutil.rmtree(staged_dir)


def load_staged_markdown(staged_id: str, context: SkillWriteContext) -> str | None:
    skill_path = _staged_dir(staged_id, context.volume_mount_path) / SKILL_FILENAME
    if not skill_path.is_file():
        return None
    return skill_path.read_text(encoding="utf-8")


def reject_skill_change(
    staged_id: str,
    context: SkillWriteContext,
    *,
    reason: str | None = None,
) -> StagedSkillChange:
    change = get_staged_change(staged_id, context)
    _assert_staged_change_owned(change, context)
    updated = mark_staged_change(staged_id, context, status=SkillApprovalStatus.REJECTED, reason=reason)
    record_audit_event(
        context=context,
        skill_name=change.skill_name,
        scope=change.scope,
        action=SkillWriteAction.REJECT,
        source_label=change.source_label or change.scope.value,
        old_content_hash=change.old_content_hash,
        new_content_hash=change.new_content_hash,
        approval_status=SkillApprovalStatus.REJECTED,
        reason=reason,
        staged_change_id=staged_id,
    )
    remove_staged_change(staged_id, context)
    return updated


def approve_skill_change(
    staged_id: str,
    context: SkillWriteContext,
    *,
    commit_fn,
) -> StagedSkillChange:
    """Approve a staged change and delegate the commit to *commit_fn*."""
    change = get_staged_change(staged_id, context)
    _assert_staged_change_owned(change, context)
    if change.action is SkillWriteAction.DELETE:
        markdown = None
    else:
        markdown = load_staged_markdown(staged_id, context)
        if markdown is None:
            raise SkillValidationError("Staged skill content is missing.", code="missing_staged_content")
    updated = mark_staged_change(staged_id, context, status=SkillApprovalStatus.APPROVED)
    if change.action is SkillWriteAction.DELETE:
        commit_fn(change, None)
    else:
        commit_fn(change, markdown)
    record_audit_event(
        context=context,
        skill_name=change.skill_name,
        scope=change.scope,
        action=SkillWriteAction.APPROVE,
        source_label=change.source_label or change.scope.value,
        old_content_hash=change.old_content_hash,
        new_content_hash=change.new_content_hash,
        approval_status=SkillApprovalStatus.APPROVED,
        staged_change_id=staged_id,
    )
    remove_staged_change(staged_id, context)
    return updated


__all__ = [
    "MANIFEST_FILENAME",
    "SKILL_FILENAME",
    "STAGING_DIR_NAME",
    "approve_skill_change",
    "create_staged_change",
    "get_staged_change",
    "list_pending_staged_changes",
    "load_staged_markdown",
    "reject_skill_change",
    "remove_staged_change",
    "staging_root",
]
