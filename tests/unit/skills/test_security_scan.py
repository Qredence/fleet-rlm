from __future__ import annotations

from fleet_rlm.skills.schemas import SkillScope, SkillSecuritySeverity
from fleet_rlm.skills.security_scan import scan_skill_bundle, scan_skill_markdown


def test_scan_skill_markdown_flags_credential_warning() -> None:
    markdown = '---\nname: alpha\ndescription: "Alpha"\n---\n\napi_key: super-secret\n'
    result = scan_skill_markdown(skill_name="alpha", scope=SkillScope.USER, markdown=markdown)
    assert any(item.code == "credential_literal" for item in result.findings)
    assert result.force_allowed is True
    assert result.blocked is False


def test_scan_skill_markdown_critical_on_oversize() -> None:
    markdown = "x" * (512 * 1024 + 1)
    result = scan_skill_markdown(skill_name="alpha", scope=SkillScope.USER, markdown=markdown)
    assert result.blocked is True
    assert result.force_allowed is False


def test_scan_skill_bundle_rejects_traversal_path() -> None:
    result = scan_skill_bundle(
        skill_name="alpha",
        scope=SkillScope.USER,
        files={"../secret.txt": b"nope"},
    )
    assert result.blocked is True
    assert result.force_allowed is False
    assert any(item.severity is SkillSecuritySeverity.CRITICAL for item in result.findings)


def test_scan_skill_bundle_rejects_community_scripts() -> None:
    result = scan_skill_bundle(
        skill_name="alpha",
        scope=SkillScope.USER,
        files={
            "SKILL.md": b"---\nname: alpha\ndescription: x\n---\n",
            "scripts/run.py": b"print('hi')\n",
        },
        community_install=True,
    )
    assert result.blocked is True
    assert any(item.code == "community_scripts" for item in result.findings)
