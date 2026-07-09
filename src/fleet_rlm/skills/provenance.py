"""Provenance sidecar storage and content hashing for installed skills."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fleet_rlm.skills.schemas import SkillProvenanceRecord, SkillScope, SkillTrustLevel

PROVENANCE_DIR_NAME = ".provenance"
_REMOTE_SOURCE_PREFIX = "remote:"


def provenance_root(volume_mount_path: str) -> Path:
    return Path(volume_mount_path) / "skills" / PROVENANCE_DIR_NAME


def provenance_path(volume_mount_path: str, scope: SkillScope, name: str) -> Path:
    return provenance_root(volume_mount_path) / scope.value / f"{name}.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def content_hash_for_markdown(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def content_hash_for_skill_dir(skill_dir: Path) -> str:
    """Stable hash over directory-style skill files in sorted path order."""
    digest = hashlib.sha256()
    if not skill_dir.is_dir():
        return digest.hexdigest()
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def read_provenance(volume_mount_path: str, scope: SkillScope, name: str) -> SkillProvenanceRecord | None:
    path = provenance_path(volume_mount_path, scope, name)
    if not path.is_file():
        return None
    return SkillProvenanceRecord.model_validate_json(path.read_text(encoding="utf-8"))


def write_provenance(volume_mount_path: str, record: SkillProvenanceRecord) -> SkillProvenanceRecord:
    path = provenance_path(volume_mount_path, record.scope, record.skill_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return record


def resolve_volume_trust_level(
    *,
    volume_mount_path: str | None,
    scope: SkillScope,
    name: str,
    source: str,
) -> SkillTrustLevel:
    """Resolve trust for a volume skill from provenance or remote source label."""
    if source.startswith(_REMOTE_SOURCE_PREFIX):
        if volume_mount_path:
            provenance = read_provenance(volume_mount_path, scope, name)
            if provenance is not None:
                return provenance.trust_level
        return SkillTrustLevel.COMMUNITY
    if volume_mount_path:
        provenance = read_provenance(volume_mount_path, scope, name)
        if provenance is not None:
            return provenance.trust_level
    return SkillTrustLevel.TRUSTED


__all__ = [
    "PROVENANCE_DIR_NAME",
    "content_hash_for_markdown",
    "content_hash_for_skill_dir",
    "provenance_path",
    "provenance_root",
    "read_provenance",
    "resolve_volume_trust_level",
    "write_provenance",
]
