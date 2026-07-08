from __future__ import annotations

from fleet_rlm.skills.schemas import (
    SkillBundle,
    SkillCatalogEntry,
    SkillMetadata,
    SkillPermissionMode,
    SkillResource,
    SkillResourceKind,
    SkillRuntimeContext,
    SkillScope,
    SkillTrustLevel,
    SkillValidationIssue,
    SkillValidationResult,
    SkillVisibilityPolicy,
)


def test_skill_scope_enum_round_trip() -> None:
    assert SkillScope.SCAFFOLD.value == "scaffold"
    assert SkillScope("user") is SkillScope.USER


def test_skill_metadata_model() -> None:
    metadata = SkillMetadata(
        name="sandbox-execution",
        description="Execute Python in Daytona sandboxes.",
        scope=SkillScope.SCAFFOLD,
        trust_level=SkillTrustLevel.TRUSTED,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source="scaffold:fleet_rlm.scaffold.skills.sandbox-execution.SKILL.md",
        directory_style=True,
    )
    assert metadata.name == "sandbox-execution"
    assert metadata.directory_style is True


def test_skill_bundle_defaults_resources() -> None:
    metadata = SkillMetadata(
        name="rlm",
        description="Hub skill",
        scope=SkillScope.SCAFFOLD,
        trust_level=SkillTrustLevel.TRUSTED,
        permission_mode=SkillPermissionMode.READ_ONLY,
        source="scaffold:rlm",
        directory_style=True,
    )
    bundle = SkillBundle(metadata=metadata, instructions="# RLM")
    assert bundle.resources == []


def test_skill_runtime_context_defaults() -> None:
    context = SkillRuntimeContext()
    assert context.volume_mount_path is None
    assert context.selected_skill_ids == []
    assert context.max_active_skills is None
    assert isinstance(context.visibility, SkillVisibilityPolicy)


def test_skill_validation_result() -> None:
    result = SkillValidationResult(
        valid=False,
        issues=[
            SkillValidationIssue(
                severity="error",
                code="invalid_name",
                message="bad name",
            )
        ],
    )
    assert result.valid is False
    assert result.issues[0].severity == "error"


def test_skill_catalog_entry() -> None:
    entry = SkillCatalogEntry(
        name="long-context",
        description="Process large context",
        scope=SkillScope.SCAFFOLD,
        trust_level=SkillTrustLevel.TRUSTED,
        source="scaffold:long-context",
    )
    assert entry.name == "long-context"


def test_skill_resource_kind() -> None:
    resource = SkillResource(kind=SkillResourceKind.REFERENCE, path="references/layout.md")
    assert resource.kind is SkillResourceKind.REFERENCE
