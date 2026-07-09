"""Update lifecycle and drift detection for remotely installed skills."""

from __future__ import annotations

from datetime import UTC, datetime

from fleet_rlm.skills.catalog import resolve_skill_directory, resolve_skill_metadata
from fleet_rlm.skills.errors import SkillInstallDeniedError, SkillNotFoundError
from fleet_rlm.skills.install import SkillInstallResult, install_skill_from_url
from fleet_rlm.skills.provenance import (
    content_hash_for_skill_dir,
    read_provenance,
    write_provenance,
)
from fleet_rlm.skills.remote_fetch import content_sha256, fetch_url_bytes, fetch_url_text
from fleet_rlm.skills.schemas import (
    SkillInstallPolicy,
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillRuntimeContext,
    SkillScope,
    SkillUpdateStatus,
    SkillWriteContext,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fetch_upstream_hash(provenance: SkillProvenanceRecord, policy: SkillInstallPolicy) -> str:
    if provenance.source is SkillInstallSource.URL_SINGLE and provenance.source_url:
        payload = fetch_url_bytes(provenance.source_url, policy=policy)
        return content_sha256(payload)
    if provenance.source is SkillInstallSource.MANIFEST and provenance.manifest_url:
        if provenance.upstream_content_hash:
            return provenance.upstream_content_hash
        raise SkillInstallDeniedError("Installed skill has no updatable remote source.")
    if provenance.source is SkillInstallSource.GITHUB_REPO and provenance.source_url:
        markdown = fetch_url_text(provenance.source_url, policy=policy)
        return content_sha256(markdown.encode("utf-8"))
    if provenance.upstream_content_hash:
        return provenance.upstream_content_hash
    raise SkillInstallDeniedError("Installed skill has no updatable remote source.")


def check_skill_update(
    *,
    skill_name: str,
    scope: SkillScope,
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
) -> SkillUpdateStatus:
    runtime = SkillRuntimeContext(volume_mount_path=context.volume_mount_path)
    metadata = resolve_skill_metadata(skill_name, runtime)
    if metadata is None:
        raise SkillNotFoundError(skill_name)
    provenance = read_provenance(context.volume_mount_path, scope, skill_name)
    if provenance is None:
        return SkillUpdateStatus(
            skill_name=skill_name,
            scope=scope,
            installed=True,
            drift_detected=False,
            provenance=None,
        )

    skill_dir = resolve_skill_directory(metadata, runtime)
    if skill_dir is not None and skill_dir.is_dir():
        if provenance.source is SkillInstallSource.URL_SINGLE and (skill_dir / "SKILL.md").is_file():
            local_hash = content_sha256((skill_dir / "SKILL.md").read_bytes())
        else:
            local_hash = content_hash_for_skill_dir(skill_dir)
    else:
        local_hash = provenance.content_hash
    try:
        upstream_hash = _fetch_upstream_hash(provenance, policy)
    except SkillInstallDeniedError:
        write_provenance(
            context.volume_mount_path,
            provenance.model_copy(
                update={
                    "last_checked_at": _utc_now_iso(),
                    "drift_detected": False,
                }
            ),
        )
        return SkillUpdateStatus(
            skill_name=skill_name,
            scope=scope,
            installed=True,
            drift_detected=False,
            content_hash=local_hash,
            upstream_content_hash=provenance.upstream_content_hash,
            provenance=provenance,
        )

    drift = upstream_hash != local_hash
    updated = provenance.model_copy(
        update={
            "upstream_content_hash": upstream_hash,
            "last_checked_at": _utc_now_iso(),
            "drift_detected": drift,
        }
    )
    write_provenance(context.volume_mount_path, updated)
    return SkillUpdateStatus(
        skill_name=skill_name,
        scope=scope,
        installed=True,
        drift_detected=drift,
        content_hash=local_hash,
        upstream_content_hash=upstream_hash,
        provenance=updated,
    )


def update_installed_skill(
    *,
    skill_name: str,
    scope: SkillScope,
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
    force: bool = False,
) -> SkillInstallResult | SkillUpdateStatus:
    status = check_skill_update(
        skill_name=skill_name,
        scope=scope,
        context=context,
        policy=policy,
    )
    if status.provenance is None:
        raise SkillInstallDeniedError("Skill was not installed from a remote source.")
    if not status.drift_detected:
        return status
    provenance = status.provenance
    if provenance.source is SkillInstallSource.URL_SINGLE and provenance.source_url:
        return install_skill_from_url(
            url=provenance.source_url,
            context=context,
            policy=policy,
            name=skill_name,
            scope=scope,
            force=force,
        )
    raise SkillInstallDeniedError("Update is only supported for URL-installed skills in this release.")


__all__ = ["check_skill_update", "update_installed_skill"]
