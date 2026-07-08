"""Skill validation — frontmatter, path safety, and bundle checks."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import unquote

from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillResource,
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
        raise ValueError("Skill name must be a simple markdown basename.")
    return normalized


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


__all__ = [
    "safe_skill_name",
    "validate_resource_path",
    "validate_skill_bundle",
    "validate_skill_metadata",
]
