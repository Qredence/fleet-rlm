from __future__ import annotations

import pytest

from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.schemas import (
    SkillMetadata,
    SkillPermissionMode,
    SkillResource,
    SkillResourceKind,
    SkillScope,
    SkillTrustLevel,
)
from fleet_rlm.skills.validator import (
    safe_skill_name,
    validate_resource_path,
    validate_skill_bundle,
    validate_skill_metadata,
)


def test_safe_skill_name_rejects_traversal() -> None:
    with pytest.raises(SkillValidationError) as exc_info:
        safe_skill_name("../escape")
    assert exc_info.value.code == "invalid_skill_name"


def test_validate_skill_metadata_accepts_kebab_case_directory_match() -> None:
    result = validate_skill_metadata(
        name="sandbox-execution",
        description="Execute Python in Daytona sandboxes.",
        directory_name="sandbox-execution",
    )
    assert result.valid is True


def test_validate_skill_metadata_accepts_legacy_flat_virtual_skill() -> None:
    result = validate_skill_metadata(
        name="legacy-flat",
        description="Legacy flat markdown skill.",
        directory_name=None,
    )
    assert result.valid is True


def test_validate_skill_metadata_rejects_directory_mismatch() -> None:
    result = validate_skill_metadata(
        name="sandbox-execution",
        description="Execute Python in Daytona sandboxes.",
        directory_name="wrong-dir",
    )
    assert result.valid is False
    assert any(issue.code == "directory_name_mismatch" for issue in result.issues)


def test_validate_resource_path_rejects_absolute_and_traversal() -> None:
    for bad_path in (
        "/etc/passwd",
        "../secrets.txt",
        "references/../../etc/passwd",
        "references\\layout.md",
        "references/%2e%2e/secret.md",
        "other-root/file.md",
    ):
        result = validate_resource_path(bad_path)
        assert result.valid is False, bad_path


def test_validate_resource_path_accepts_approved_root() -> None:
    result = validate_resource_path("references/volume-layout.md")
    assert result.valid is True


def test_validate_skill_bundle_warns_on_vague_description() -> None:
    metadata = SkillMetadata(
        name="short",
        description="skill",
        scope=SkillScope.USER,
        trust_level=SkillTrustLevel.TRUSTED,
        permission_mode=SkillPermissionMode.READ_WRITE,
        source="user:/tmp/skills/user/short/SKILL.md",
        directory_style=True,
    )
    result = validate_skill_bundle(metadata, [], raw_markdown="# Short")
    assert result.valid is True
    assert any(issue.code == "vague_description" for issue in result.issues)


def test_validate_skill_bundle_warns_on_undocumented_script() -> None:
    metadata = SkillMetadata(
        name="diagnostics",
        description="Debug runtime failures and contract drift.",
        scope=SkillScope.SCAFFOLD,
        trust_level=SkillTrustLevel.TRUSTED,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source="scaffold:diagnostics",
        directory_style=True,
    )
    resources = [SkillResource(kind=SkillResourceKind.SCRIPT, path="scripts/diagnose.py")]
    result = validate_skill_bundle(metadata, resources, raw_markdown="# Diagnostics")
    assert result.valid is True
    assert any(issue.code == "undocumented_script" for issue in result.issues)
