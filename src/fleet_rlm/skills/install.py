"""Remote skill install orchestration."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fleet_rlm.skills.audit import record_audit_event
from fleet_rlm.skills.bundle_manifest import (
    SkillBundleManifest,
    manifest_from_json_text,
    parse_bundle_manifest,
    verify_bundle_files,
)
from fleet_rlm.skills.catalog import parse_skill_frontmatter, resolve_skill_metadata
from fleet_rlm.skills.errors import (
    SkillInstallBlockedError,
    SkillInstallDeniedError,
    SkillNotFoundError,
    SkillProtectedError,
    SkillQuarantinedError,
    SkillValidationError,
)
from fleet_rlm.skills.loader import clear_skill_cache
from fleet_rlm.skills.permissions import is_skill_protected, requires_staging
from fleet_rlm.skills.provenance import (
    content_hash_for_markdown,
    content_hash_for_skill_dir,
    write_provenance,
)
from fleet_rlm.skills.quarantine import quarantine_install, store_scan_result
from fleet_rlm.skills.remote_fetch import (
    content_sha256,
    fetch_github_skill_markdown,
    fetch_tap_index,
    fetch_url_bytes,
    fetch_url_text,
)
from fleet_rlm.skills.schemas import (
    SkillInstallAction,
    SkillInstallPolicy,
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillRuntimeContext,
    SkillScope,
    SkillSecurityScanResult,
    SkillSecuritySeverity,
    SkillTrustLevel,
    SkillWriteAction,
    SkillWriteContext,
)
from fleet_rlm.skills.security_scan import scan_skill_bundle, scan_skill_markdown
from fleet_rlm.skills.validator import safe_skill_name, validate_skill_markdown, validate_write_target_path
from fleet_rlm.skills.writes import (
    _ensure_create_does_not_shadow_existing,
    _ensure_scope_writable,
    write_skill_for_scope,
)

_REMOTE_SOURCE_LABEL = "remote"


def _ensure_direct_remote_install(context: SkillWriteContext, scope: SkillScope) -> None:
    _ensure_scope_writable(scope, context)
    if requires_staging(context):
        raise SkillInstallDeniedError(
            "Remote installs require direct commit; staging must be disabled.",
            code="skill_install_staging_required",
        )


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    skill_name: str
    scope: SkillScope
    committed: bool
    content_hash: str
    scan: SkillSecurityScanResult
    provenance: SkillProvenanceRecord


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _skills_root(context: SkillWriteContext) -> Path:
    return Path(context.volume_mount_path) / "skills"


def _scope_root(context: SkillWriteContext, scope: SkillScope) -> Path:
    return _skills_root(context) / scope.value


def _skill_directory(context: SkillWriteContext, scope: SkillScope, name: str) -> Path:
    return _scope_root(context, scope) / name


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_bytes(payload)
    os.replace(temp_path, path)


def _validate_install_scan(scan: SkillSecurityScanResult, *, force: bool) -> None:
    if scan.blocked and not force:
        raise SkillInstallBlockedError(scan_id=scan.scan_id)
    if not scan.force_allowed and force:
        raise SkillInstallBlockedError(
            "Dangerous remote skill cannot be installed with force.",
            code="skill_install_force_denied",
            scan_id=scan.scan_id,
        )
    warning_findings = [item for item in scan.findings if item.severity is SkillSecuritySeverity.WARNING]
    if warning_findings and not force and not scan.blocked:
        raise SkillInstallBlockedError(scan_id=scan.scan_id)


def _record_install_audit(
    *,
    context: SkillWriteContext,
    skill_name: str,
    scope: SkillScope,
    action: SkillInstallAction,
    source_label: str,
    old_hash: str | None,
    new_hash: str,
    reason: str | None = None,
) -> None:
    record_audit_event(
        context=context,
        skill_name=skill_name,
        scope=scope,
        action=action,
        source_label=source_label,
        old_content_hash=old_hash,
        new_content_hash=new_hash,
        reason=reason,
    )


def _write_provenance_record(
    *,
    context: SkillWriteContext,
    skill_name: str,
    scope: SkillScope,
    source: SkillInstallSource,
    content_hash: str,
    scan_id: str | None,
    source_url: str | None = None,
    repo: str | None = None,
    ref: str | None = None,
    subpath: str | None = None,
    manifest_url: str | None = None,
    tap_name: str | None = None,
    upstream_content_hash: str | None = None,
) -> SkillProvenanceRecord:
    now = _utc_now_iso()
    record = SkillProvenanceRecord(
        skill_name=skill_name,
        scope=scope,
        source=source,
        source_url=source_url,
        repo=repo,
        ref=ref,
        subpath=subpath,
        manifest_url=manifest_url,
        tap_name=tap_name,
        trust_level=SkillTrustLevel.COMMUNITY,
        content_hash=content_hash,
        upstream_content_hash=upstream_content_hash or content_hash,
        installed_at=now,
        updated_at=now,
        last_checked_at=now,
        drift_detected=False,
        scan_id=scan_id,
    )
    return write_provenance(context.volume_mount_path, record)


def commit_installed_bundle(
    *,
    context: SkillWriteContext,
    scope: SkillScope,
    skill_name: str,
    files: dict[str, bytes],
    provenance: SkillProvenanceRecord,
    scan: SkillSecurityScanResult,
    action: SkillInstallAction = SkillInstallAction.INSTALL,
) -> SkillInstallResult:
    normalized = safe_skill_name(skill_name)
    _ensure_direct_remote_install(context, scope)
    scope_root = _scope_root(context, scope)
    target_dir = _skill_directory(context, scope, normalized)
    validation = validate_write_target_path(scope_root, target_dir)
    if not validation.valid:
        issue = validation.issues[0]
        raise SkillValidationError(issue.message, code=issue.code)

    skill_md = files.get("SKILL.md")
    if skill_md is None:
        raise SkillValidationError("Bundle must include SKILL.md.", code="missing_skill_md")
    markdown = skill_md.decode("utf-8")
    markdown_result = validate_skill_markdown(markdown, directory_name=normalized)
    if not markdown_result.valid:
        issue = next(item for item in markdown_result.issues if item.severity == "error")
        raise SkillValidationError(issue.message, code=issue.code)
    parsed_name, _ = parse_skill_frontmatter(markdown)
    if parsed_name and parsed_name != normalized:
        raise SkillValidationError(
            f"Skill directory '{normalized}' must match frontmatter name '{parsed_name}'.",
            code="directory_name_mismatch",
        )

    skill_md_path = target_dir / "SKILL.md"
    is_update = skill_md_path.is_file()
    if not is_update:
        _ensure_create_does_not_shadow_existing(normalized, context, scope)
    else:
        metadata = resolve_skill_metadata(
            normalized,
            SkillRuntimeContext(volume_mount_path=context.volume_mount_path),
        )
        if metadata is not None and is_skill_protected(metadata) and metadata.scope is not scope:
            raise SkillProtectedError()

    old_hash: str | None = None
    if target_dir.is_dir():
        old_hash = content_hash_for_skill_dir(target_dir)
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, payload in sorted(files.items()):
        _atomic_write_bytes(target_dir / relative_path, payload)

    content_hash = content_hash_for_skill_dir(target_dir)
    clear_skill_cache()
    write_provenance(context.volume_mount_path, provenance.model_copy(update={"content_hash": content_hash}))
    _record_install_audit(
        context=context,
        skill_name=normalized,
        scope=scope,
        action=action,
        source_label=f"{_REMOTE_SOURCE_LABEL}:{provenance.source.value}",
        old_hash=old_hash,
        new_hash=content_hash,
    )
    store_scan_result(context.volume_mount_path, scan)
    return SkillInstallResult(
        skill_name=normalized,
        scope=scope,
        committed=True,
        content_hash=content_hash,
        scan=scan,
        provenance=provenance.model_copy(update={"content_hash": content_hash}),
    )


def _resolve_skill_name_from_markdown(markdown: str, requested_name: str | None) -> str:
    parsed_name, _ = parse_skill_frontmatter(markdown)
    if parsed_name:
        return safe_skill_name(parsed_name)
    if requested_name:
        return safe_skill_name(requested_name)
    raise SkillValidationError("Skill name is required.", code="missing_name")


def install_skill_from_url(
    *,
    url: str,
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
    name: str | None = None,
    scope: SkillScope = SkillScope.USER,
    force: bool = False,
) -> SkillInstallResult:
    if not policy.url_install_enabled:
        raise SkillInstallDeniedError("Remote URL skill install is disabled.")
    if scope not in {SkillScope.USER, SkillScope.SESSION}:
        raise SkillInstallDeniedError("Remote installs are only supported for user or session scope.")
    _ensure_direct_remote_install(context, scope)

    markdown = fetch_url_text(url, policy=policy)
    skill_name = _resolve_skill_name_from_markdown(markdown, name)
    content_hash = content_hash_for_markdown(markdown)
    scan = scan_skill_markdown(
        skill_name=skill_name,
        scope=scope,
        markdown=markdown,
        content_hash=content_hash,
        community_install=True,
    )
    store_scan_result(context.volume_mount_path, scan)
    try:
        _validate_install_scan(scan, force=force)
    except SkillInstallBlockedError:
        quarantine_install(
            volume_mount_path=context.volume_mount_path,
            scan=scan,
            payload={"url": url, "name": skill_name},
        )
        raise SkillQuarantinedError(scan_id=scan.scan_id) from None

    existing = _skill_directory(context, scope, skill_name)
    if existing.is_dir() and any(
        path.is_file() and path.relative_to(existing).as_posix() != "SKILL.md" for path in existing.rglob("*")
    ):
        shutil.rmtree(existing)
    write_action = SkillWriteAction.UPDATE if (existing / "SKILL.md").is_file() else SkillWriteAction.CREATE
    write_skill_for_scope(
        scope=scope,
        action=write_action,
        name=skill_name,
        raw_markdown=markdown,
        context=context,
        reason=f"remote:url:{url}",
    )
    provenance = _write_provenance_record(
        context=context,
        skill_name=skill_name,
        scope=scope,
        source=SkillInstallSource.URL_SINGLE,
        content_hash=content_hash,
        scan_id=scan.scan_id,
        source_url=url,
        upstream_content_hash=content_hash,
    )
    return SkillInstallResult(
        skill_name=skill_name,
        scope=scope,
        committed=True,
        content_hash=content_hash,
        scan=scan,
        provenance=provenance,
    )


def install_skill_from_manifest(
    *,
    manifest: SkillBundleManifest | dict[str, Any] | str,
    files: dict[str, bytes],
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
    scope: SkillScope = SkillScope.USER,
    force: bool = False,
    manifest_url: str | None = None,
) -> SkillInstallResult:
    if not policy.bundle_install_enabled:
        raise SkillInstallDeniedError("Remote bundle skill install is disabled.")
    if scope not in {SkillScope.USER, SkillScope.SESSION}:
        raise SkillInstallDeniedError("Remote installs are only supported for user or session scope.")
    parsed = parse_bundle_manifest(manifest) if not isinstance(manifest, SkillBundleManifest) else manifest
    verify_bundle_files(parsed, files)
    bundle_bytes = sum(len(payload) for payload in files.values())
    if bundle_bytes > policy.max_bundle_bytes:
        raise SkillInstallDeniedError("Remote bundle exceeds the maximum allowed size.")
    skill_name = safe_skill_name(parsed.name)
    content_hash = content_sha256(b"".join(files[path] for path in sorted(files)))
    scan = scan_skill_bundle(
        skill_name=skill_name,
        scope=scope,
        files=files,
        content_hash=content_hash,
        community_install=True,
    )
    store_scan_result(context.volume_mount_path, scan)
    try:
        _validate_install_scan(scan, force=force)
    except SkillInstallBlockedError:
        quarantine_install(
            volume_mount_path=context.volume_mount_path,
            scan=scan,
            payload={"manifest_url": manifest_url, "name": skill_name},
        )
        raise SkillQuarantinedError(scan_id=scan.scan_id) from None

    provenance = _write_provenance_record(
        context=context,
        skill_name=skill_name,
        scope=scope,
        source=SkillInstallSource.MANIFEST,
        content_hash=content_hash,
        scan_id=scan.scan_id,
        manifest_url=manifest_url,
        upstream_content_hash=content_hash,
    )
    return commit_installed_bundle(
        context=context,
        scope=scope,
        skill_name=skill_name,
        files=files,
        provenance=provenance,
        scan=scan,
    )


def install_skill_from_repo(
    *,
    repo_url: str,
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
    scope: SkillScope = SkillScope.USER,
    force: bool = False,
) -> SkillInstallResult:
    if not policy.bundle_install_enabled:
        raise SkillInstallDeniedError("Remote bundle skill install is disabled.")
    markdown, owner, repo, ref, raw_url = fetch_github_skill_markdown(repo_url=repo_url, policy=policy)
    skill_name = _resolve_skill_name_from_markdown(markdown, None)
    files = {"SKILL.md": markdown.encode("utf-8")}
    manifest = SkillBundleManifest(
        name=skill_name,
        files={"SKILL.md": content_sha256(files["SKILL.md"])},
    )
    result = install_skill_from_manifest(
        manifest=manifest,
        files=files,
        context=context,
        policy=policy,
        scope=scope,
        force=force,
        manifest_url=repo_url,
    )
    provenance = result.provenance.model_copy(
        update={
            "source": SkillInstallSource.GITHUB_REPO,
            "repo": f"{owner}/{repo}",
            "ref": ref,
            "source_url": raw_url,
            "manifest_url": repo_url,
        }
    )
    write_provenance(context.volume_mount_path, provenance)
    return SkillInstallResult(
        skill_name=result.skill_name,
        scope=result.scope,
        committed=result.committed,
        content_hash=result.content_hash,
        scan=result.scan,
        provenance=provenance,
    )


def install_skill_from_tap(
    *,
    tap_skill_name: str,
    context: SkillWriteContext,
    policy: SkillInstallPolicy,
    scope: SkillScope = SkillScope.USER,
    force: bool = False,
) -> SkillInstallResult:
    if not policy.bundle_install_enabled:
        raise SkillInstallDeniedError("Remote bundle skill install is disabled.")
    if not policy.tap_url:
        raise SkillInstallDeniedError("Remote skill tap URL is not configured.")
    index = fetch_tap_index(policy.tap_url, policy=policy)
    entries = index.get("skills")
    if not isinstance(entries, list):
        raise SkillValidationError("Remote skill tap index is invalid.", code="invalid_tap_index")
    selected: dict[str, Any] | None = None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == tap_skill_name:
            selected = entry
            break
    if selected is None:
        raise SkillNotFoundError(tap_skill_name)
    manifest_url = selected.get("manifest_url")
    if not isinstance(manifest_url, str) or not manifest_url:
        raise SkillValidationError("Tap entry is missing manifest_url.", code="invalid_tap_entry")
    manifest_text = fetch_url_text(manifest_url, policy=policy, max_bytes=policy.max_bundle_bytes)
    manifest = manifest_from_json_text(manifest_text)
    files: dict[str, bytes] = {}
    for relative_path in manifest.files:
        file_url = manifest_url.rsplit("/", 1)[0] + "/" + relative_path
        files[relative_path] = fetch_url_bytes(file_url, policy=policy, max_bytes=policy.max_bundle_bytes)
    result = install_skill_from_manifest(
        manifest=manifest,
        files=files,
        context=context,
        policy=policy,
        scope=scope,
        force=force,
        manifest_url=manifest_url,
    )
    provenance = result.provenance.model_copy(
        update={
            "source": SkillInstallSource.TAP,
            "tap_name": tap_skill_name,
            "source_url": policy.tap_url,
        }
    )
    write_provenance(context.volume_mount_path, provenance)
    return SkillInstallResult(
        skill_name=result.skill_name,
        scope=result.scope,
        committed=result.committed,
        content_hash=result.content_hash,
        scan=result.scan,
        provenance=provenance,
    )


__all__ = [
    "SkillInstallResult",
    "commit_installed_bundle",
    "install_skill_from_manifest",
    "install_skill_from_repo",
    "install_skill_from_tap",
    "install_skill_from_url",
]
