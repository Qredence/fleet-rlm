"""Append-only audit metadata for skill write operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fleet_rlm.skills.schemas import (
    SkillApprovalStatus,
    SkillAuditRecord,
    SkillInstallAction,
    SkillScope,
    SkillWriteAction,
    SkillWriteContext,
)

AUDIT_DIR_NAME = ".audit"
AUDIT_LOG_FILENAME = "events.jsonl"


def audit_log_path(volume_mount_path: str) -> Path:
    return Path(volume_mount_path) / "skills" / AUDIT_DIR_NAME / AUDIT_LOG_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def record_audit_event(
    *,
    context: SkillWriteContext,
    skill_name: str,
    scope: SkillScope,
    action: SkillWriteAction | SkillInstallAction,
    source_label: str,
    old_content_hash: str | None = None,
    new_content_hash: str | None = None,
    approval_status: SkillApprovalStatus | None = None,
    reason: str | None = None,
    staged_change_id: str | None = None,
) -> SkillAuditRecord:
    """Persist one audit record and return the structured entry."""
    record = SkillAuditRecord(
        timestamp=_utc_now_iso(),
        skill_name=skill_name,
        scope=scope,
        action=action,
        actor=context.actor,
        user_id=context.user_id,
        session_id=context.session_id,
        workspace_id=context.workspace_id,
        org_id=context.org_id,
        old_content_hash=old_content_hash,
        new_content_hash=new_content_hash,
        source_label=source_label,
        approval_status=approval_status,
        reason=reason,
        staged_change_id=staged_change_id,
    )
    log_path = audit_log_path(context.volume_mount_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json())
        handle.write("\n")
    return record


def read_audit_events(volume_mount_path: str) -> list[SkillAuditRecord]:
    """Load audit events for tests and diagnostics."""
    log_path = audit_log_path(volume_mount_path)
    if not log_path.is_file():
        return []
    records: list[SkillAuditRecord] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(SkillAuditRecord.model_validate(json.loads(line)))
    return records


__all__ = [
    "AUDIT_DIR_NAME",
    "AUDIT_LOG_FILENAME",
    "audit_log_path",
    "read_audit_events",
    "record_audit_event",
]
