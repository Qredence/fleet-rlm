"""Skill bundle manifest parsing for multi-file remote installs."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.validator import validate_resource_path


class SkillBundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    files: dict[str, str] = Field(default_factory=dict, description="Relative path to sha256 hex digest.")


def parse_bundle_manifest(payload: str | bytes | dict[str, object]) -> SkillBundleManifest:
    if isinstance(payload, dict):
        manifest = SkillBundleManifest.model_validate(payload)
    elif isinstance(payload, bytes):
        manifest = SkillBundleManifest.model_validate_json(payload.decode("utf-8"))
    else:
        manifest = SkillBundleManifest.model_validate_json(payload)
    if "SKILL.md" not in manifest.files:
        raise SkillValidationError("Bundle manifest must include SKILL.md.", code="missing_skill_md")
    for relative_path in manifest.files:
        if relative_path == "SKILL.md":
            continue
        result = validate_resource_path(relative_path)
        if not result.valid:
            issue = result.issues[0]
            raise SkillValidationError(issue.message, code=issue.code)
    return manifest


def verify_bundle_files(manifest: SkillBundleManifest, files: dict[str, bytes]) -> None:
    for relative_path, expected_hash in manifest.files.items():
        payload = files.get(relative_path)
        if payload is None:
            raise SkillValidationError(
                f"Bundle file '{relative_path}' is missing.",
                code="missing_bundle_file",
            )
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_hash.lower():
            raise SkillValidationError(
                f"Bundle file '{relative_path}' failed integrity check.",
                code="bundle_integrity_mismatch",
            )


def manifest_from_json_text(text: str) -> SkillBundleManifest:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillValidationError("Bundle manifest JSON is invalid.", code="invalid_bundle_manifest") from exc
    if not isinstance(payload, dict):
        raise SkillValidationError("Bundle manifest JSON is invalid.", code="invalid_bundle_manifest")
    return parse_bundle_manifest(payload)


__all__ = [
    "SkillBundleManifest",
    "manifest_from_json_text",
    "parse_bundle_manifest",
    "verify_bundle_files",
]
