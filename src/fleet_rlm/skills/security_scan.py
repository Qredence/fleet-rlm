"""Static security scanning for remote skill installs."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fleet_rlm.skills.schemas import (
    SkillScope,
    SkillSecurityFinding,
    SkillSecurityScanResult,
    SkillSecuritySeverity,
)
from fleet_rlm.skills.validator import validate_resource_path

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)BEGIN (RSA |OPENSSH )?PRIVATE KEY"),
)
_EXEC_PATTERNS = (
    re.compile(r"(?i)\bos\.system\s*\("),
    re.compile(r"(?i)\bsubprocess\.(run|Popen|call)\s*\("),
    re.compile(r"(?i)\beval\s*\("),
    re.compile(r"(?i)\bexec\s*\("),
)
_EXFIL_PATTERNS = (re.compile(r"https?://[^\s)\"']+"),)
_MAX_SINGLE_FILE_BYTES = 512 * 1024
_MAX_BUNDLE_FILE_BYTES = 1024 * 1024


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _finding(
    *,
    severity: SkillSecuritySeverity,
    code: str,
    message: str,
    path: str | None = None,
) -> SkillSecurityFinding:
    return SkillSecurityFinding(severity=severity, code=code, message=message, path=path)


def _finalize_scan(
    *,
    skill_name: str,
    scope: SkillScope,
    findings: list[SkillSecurityFinding],
    content_hash: str | None = None,
) -> SkillSecurityScanResult:
    has_critical = any(item.severity is SkillSecuritySeverity.CRITICAL for item in findings)
    return SkillSecurityScanResult(
        scan_id=uuid.uuid4().hex,
        skill_name=skill_name,
        scope=scope,
        findings=findings,
        blocked=has_critical,
        force_allowed=not has_critical,
        scanned_at=_utc_now_iso(),
        content_hash=content_hash,
    )


def scan_skill_markdown(
    *,
    skill_name: str,
    scope: SkillScope,
    markdown: str,
    content_hash: str | None = None,
    community_install: bool = True,
) -> SkillSecurityScanResult:
    findings: list[SkillSecurityFinding] = []
    if len(markdown.encode("utf-8")) > _MAX_SINGLE_FILE_BYTES:
        findings.append(
            _finding(
                severity=SkillSecuritySeverity.CRITICAL,
                code="oversize_skill_md",
                message="SKILL.md exceeds the maximum allowed size.",
            )
        )
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(markdown):
            findings.append(
                _finding(
                    severity=SkillSecuritySeverity.WARNING,
                    code="credential_literal",
                    message="Potential credential or secret literal detected in SKILL.md.",
                )
            )
            break
    for pattern in _EXEC_PATTERNS:
        if pattern.search(markdown):
            findings.append(
                _finding(
                    severity=SkillSecuritySeverity.WARNING,
                    code="exec_pattern",
                    message="Potential code execution pattern detected in SKILL.md.",
                )
            )
            break
    if community_install:
        for pattern in _EXFIL_PATTERNS:
            if pattern.search(markdown):
                findings.append(
                    _finding(
                        severity=SkillSecuritySeverity.INFO,
                        code="external_url",
                        message="External URL reference detected in SKILL.md.",
                    )
                )
                break
    return _finalize_scan(
        skill_name=skill_name,
        scope=scope,
        findings=findings,
        content_hash=content_hash,
    )


def scan_skill_bundle(
    *,
    skill_name: str,
    scope: SkillScope,
    files: dict[str, bytes],
    content_hash: str | None = None,
    community_install: bool = True,
) -> SkillSecurityScanResult:
    findings: list[SkillSecurityFinding] = []
    has_scripts = False
    for relative_path, payload in sorted(files.items()):
        if relative_path == "SKILL.md":
            text = payload.decode("utf-8", errors="replace")
            markdown_scan = scan_skill_markdown(
                skill_name=skill_name,
                scope=scope,
                markdown=text,
                content_hash=content_hash,
                community_install=community_install,
            )
            findings.extend(markdown_scan.findings)
            continue
        validation = validate_resource_path(relative_path)
        if not validation.valid:
            issue = validation.issues[0]
            findings.append(
                _finding(
                    severity=SkillSecuritySeverity.CRITICAL,
                    code=issue.code,
                    message=issue.message,
                    path=relative_path,
                )
            )
            continue
        if len(payload) > _MAX_BUNDLE_FILE_BYTES:
            findings.append(
                _finding(
                    severity=SkillSecuritySeverity.CRITICAL,
                    code="oversize_bundle_file",
                    message="Bundle file exceeds the maximum allowed size.",
                    path=relative_path,
                )
            )
        if relative_path.startswith("scripts/"):
            has_scripts = True
            text = payload.decode("utf-8", errors="replace")
            for pattern in _CREDENTIAL_PATTERNS:
                if pattern.search(text):
                    findings.append(
                        _finding(
                            severity=SkillSecuritySeverity.WARNING,
                            code="credential_literal",
                            message="Potential credential or secret literal detected in bundle file.",
                            path=relative_path,
                        )
                    )
                    break
            for pattern in _EXEC_PATTERNS:
                if pattern.search(text):
                    findings.append(
                        _finding(
                            severity=SkillSecuritySeverity.WARNING,
                            code="exec_pattern",
                            message="Potential code execution pattern detected in bundle file.",
                            path=relative_path,
                        )
                    )
                    break
    if community_install and has_scripts:
        findings.append(
            _finding(
                severity=SkillSecuritySeverity.CRITICAL,
                code="community_scripts",
                message="Community remote installs cannot include scripts/ resources.",
            )
        )
    return _finalize_scan(
        skill_name=skill_name,
        scope=scope,
        findings=findings,
        content_hash=content_hash,
    )


__all__ = ["scan_skill_bundle", "scan_skill_markdown"]
