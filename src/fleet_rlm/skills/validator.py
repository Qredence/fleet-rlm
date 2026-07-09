"""Skill validation — frontmatter, path safety, and bundle checks."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import unquote

from fleet_rlm.skills.errors import SkillResourcePathError, SkillValidationError
from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillPermissionMode,
    SkillResource,
    SkillScope,
    SkillTrustLevel,
    SkillValidationIssue,
    SkillValidationResult,
)

_KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_APPROVED_RESOURCE_ROOTS = frozenset({"references", "scripts", "assets", "templates"})
_MAX_SKILL_MD_BYTES = 50 * 1024
_VAGUE_DESCRIPTION_MAX_LEN = 10
_GENERIC_DESCRIPTIONS = frozenset(
    {
        "skill",
        "a skill",
        "bundled fleet-rlm skill",
        "todo",
        "tbd",
        "description",
    }
)


def safe_skill_name(name: str) -> str:
    """Normalize and validate a skill basename (no path components)."""
    normalized = name.strip().removesuffix(".md")
    if not normalized or "/" in normalized or "\\" in normalized or ".." in normalized:
        raise SkillValidationError(
            "Skill name must be a simple markdown basename.",
            code="invalid_skill_name",
        )
    return normalized


def require_valid_resource_path(path: str) -> None:
    """Raise ``SkillResourcePathError`` when a resource path fails validation."""
    result = validate_resource_path(path)
    if result.valid:
        return
    issue = result.issues[0]
    raise SkillResourcePathError(issue.message, code=issue.code)


def _issue(
    *,
    severity: Literal["error", "warning"],
    code: str,
    message: str,
    path: str | None = None,
) -> SkillValidationIssue:
    return SkillValidationIssue(severity=severity, code=code, message=message, path=path)


def _merge_results(*results: SkillValidationResult) -> SkillValidationResult:
    issues: list[SkillValidationIssue] = []
    for result in results:
        issues.extend(result.issues)
    return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


def validate_skill_metadata(
    *,
    name: str,
    description: str | None,
    directory_name: str | None,
) -> SkillValidationResult:
    issues: list[SkillValidationIssue] = []
    normalized = name.strip()
    if not normalized:
        issues.append(_issue(severity="error", code="missing_name", message="Skill name is required."))
    elif not _KEBAB_CASE_RE.match(normalized):
        issues.append(
            _issue(
                severity="error",
                code="invalid_name",
                message="Skill name must be lowercase kebab-case.",
            )
        )
    if not (description or "").strip():
        issues.append(
            _issue(
                severity="error",
                code="missing_description",
                message="Skill description is required.",
            )
        )
    if directory_name is not None and normalized and directory_name != normalized:
        issues.append(
            _issue(
                severity="error",
                code="directory_name_mismatch",
                message=f"Skill directory '{directory_name}' must match frontmatter name '{normalized}'.",
            )
        )
    return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


def validate_resource_path(path: str) -> SkillValidationResult:
    issues: list[SkillValidationIssue] = []
    raw = path.strip()
    if not raw:
        issues.append(
            _issue(
                severity="error",
                code="empty_path",
                message="Resource path must not be empty.",
                path=path,
            )
        )
        return SkillValidationResult(valid=False, issues=issues)

    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        issues.append(
            _issue(
                severity="error",
                code="absolute_path",
                message="Absolute resource paths are not allowed.",
                path=path,
            )
        )

    if "\\" in raw:
        issues.append(
            _issue(
                severity="error",
                code="backslash_path",
                message="Backslash resource paths are not allowed.",
                path=path,
            )
        )

    decoded = unquote(raw)
    if ".." in decoded.split("/"):
        issues.append(
            _issue(
                severity="error",
                code="traversal",
                message="Path traversal segments are not allowed.",
                path=path,
            )
        )

    if "%2e%2e" in raw.lower() or "%2f" in raw.lower():
        issues.append(
            _issue(
                severity="error",
                code="encoded_traversal",
                message="URL-encoded traversal segments are not allowed.",
                path=path,
            )
        )

    parts = [part for part in decoded.split("/") if part]
    if parts and parts[0] not in _APPROVED_RESOURCE_ROOTS:
        issues.append(
            _issue(
                severity="error",
                code="unapproved_root",
                message=f"Resource path must start with one of: {', '.join(sorted(_APPROVED_RESOURCE_ROOTS))}.",
                path=path,
            )
        )

    return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


def validate_skill_bundle(
    metadata: SkillMetadata,
    resources: list[SkillResource],
    *,
    raw_markdown: str,
) -> SkillValidationResult:
    meta_result = validate_skill_metadata(
        name=metadata.name,
        description=metadata.description,
        directory_name=metadata.name if metadata.directory_style else None,
    )
    resource_results = [validate_resource_path(resource.path) for resource in resources]
    issues = list(meta_result.issues)
    for result in resource_results:
        issues.extend(result.issues)

    description = metadata.description.strip()
    if description and (len(description) < _VAGUE_DESCRIPTION_MAX_LEN or description.lower() in _GENERIC_DESCRIPTIONS):
        issues.append(
            _issue(
                severity="warning",
                code="vague_description",
                message="Skill description is vague or too short.",
            )
        )

    if len(raw_markdown.encode("utf-8")) > _MAX_SKILL_MD_BYTES:
        issues.append(
            _issue(
                severity="warning",
                code="oversized_skill_md",
                message="SKILL.md exceeds recommended size.",
            )
        )

    reference_paths = {resource.path for resource in resources if resource.kind.value == "reference"}
    for resource in resources:
        if resource.kind.value != "script":
            continue
        script_stem = resource.path.rsplit("/", 1)[-1]
        documented = bool(resource.description and resource.description.strip())
        if not documented:
            for ref_path in reference_paths:
                if script_stem in ref_path:
                    documented = True
                    break
        if not documented:
            issues.append(
                _issue(
                    severity="warning",
                    code="undocumented_script",
                    message=f"Script '{resource.path}' has no description or reference documentation.",
                    path=resource.path,
                )
            )

    return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


def validate_skill_markdown(raw_markdown: str, *, directory_name: str | None = None) -> SkillValidationResult:
    """Validate SKILL.md frontmatter and bundle constraints for writes."""
    from fleet_rlm.skills.catalog import parse_skill_frontmatter

    name, description = parse_skill_frontmatter(raw_markdown)
    meta_result = validate_skill_metadata(
        name=name or "",
        description=description,
        directory_name=directory_name,
    )
    if not name:
        return meta_result
    metadata = SkillMetadata(
        name=name,
        description=description or "",
        scope=SkillScope.USER,
        trust_level=SkillTrustLevel.COMMUNITY,
        permission_mode=SkillPermissionMode.READ_WRITE,
        source="write:validate",
        directory_style=True,
    )
    return _merge_results(
        meta_result,
        validate_skill_bundle(metadata, [], raw_markdown=raw_markdown),
    )


def validate_write_target_path(scope_root: Path, target: Path) -> SkillValidationResult:
    """Ensure a write target stays within an allowed scope root without symlink escape."""
    issues: list[SkillValidationIssue] = []
    raw = target.as_posix()
    if "\\" in raw:
        issues.append(
            _issue(
                severity="error",
                code="backslash_path",
                message="Backslash skill write paths are not allowed.",
                path=raw,
            )
        )

    decoded = unquote(raw)
    if ".." in PurePosixPath(decoded).parts:
        issues.append(
            _issue(
                severity="error",
                code="traversal",
                message="Path traversal segments are not allowed.",
                path=raw,
            )
        )

    if "%2e%2e" in raw.lower() or "%2f" in raw.lower():
        issues.append(
            _issue(
                severity="error",
                code="encoded_traversal",
                message="URL-encoded traversal segments are not allowed.",
                path=raw,
            )
        )

    try:
        resolved_root = scope_root.resolve()
        resolved_target = target.resolve()
        relative = resolved_target.relative_to(resolved_root)
    except ValueError:
        if target.is_absolute():
            issues.append(
                _issue(
                    severity="error",
                    code="absolute_path",
                    message="Absolute skill write paths are not allowed.",
                    path=raw,
                )
            )
        issues.append(
            _issue(
                severity="error",
                code="outside_scope_root",
                message="Skill write path escapes the allowed scope root.",
                path=raw,
            )
        )
        return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)
    except OSError:
        issues.append(
            _issue(
                severity="error",
                code="path_resolution_failed",
                message="Skill write path could not be resolved safely.",
                path=raw,
            )
        )
        return SkillValidationResult(valid=False, issues=issues)

    if ".." in relative.parts:
        issues.append(
            _issue(
                severity="error",
                code="traversal",
                message="Path traversal segments are not allowed.",
                path=raw,
            )
        )

    return SkillValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)


__all__ = [
    "require_valid_resource_path",
    "safe_skill_name",
    "validate_resource_path",
    "validate_skill_bundle",
    "validate_skill_markdown",
    "validate_skill_metadata",
    "validate_write_target_path",
]
