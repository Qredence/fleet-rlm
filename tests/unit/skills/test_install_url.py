from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.skills.errors import SkillInstallDeniedError, SkillQuarantinedError
from fleet_rlm.skills.install import install_skill_from_url
from fleet_rlm.skills.provenance import read_provenance
from fleet_rlm.skills.schemas import SkillInstallPolicy, SkillScope, SkillWriteContext


def _markdown(name: str) -> str:
    return f'---\nname: {name}\ndescription: "Remote skill"\n---\n\n# {name}\n'


def _context(volume: Path) -> SkillWriteContext:
    return SkillWriteContext(volume_mount_path=str(volume), user_id="user-1")


def test_install_skill_from_url_disabled_by_policy(tmp_path: Path) -> None:
    with pytest.raises(SkillInstallDeniedError):
        install_skill_from_url(
            url="https://example.com/SKILL.md",
            context=_context(tmp_path),
            policy=SkillInstallPolicy(url_install_enabled=False),
        )


def test_install_skill_from_url_commits_with_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = _markdown("remote-alpha")

    monkeypatch.setattr(
        "fleet_rlm.skills.install.fetch_url_text",
        lambda url, policy, max_bytes=None: markdown,
    )

    result = install_skill_from_url(
        url="https://example.com/remote-alpha/SKILL.md",
        context=_context(volume),
        policy=SkillInstallPolicy(url_install_enabled=True),
    )
    assert result.committed is True
    assert (volume / "skills" / "user" / "remote-alpha" / "SKILL.md").is_file()
    provenance = read_provenance(str(volume), SkillScope.USER, "remote-alpha")
    assert provenance is not None
    assert provenance.source_url == "https://example.com/remote-alpha/SKILL.md"


def test_install_skill_from_url_quarantines_critical_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = "x" * (512 * 1024 + 10)

    monkeypatch.setattr(
        "fleet_rlm.skills.install.fetch_url_text",
        lambda url, policy, max_bytes=None: markdown,
    )

    with pytest.raises(SkillQuarantinedError):
        install_skill_from_url(
            url="https://example.com/SKILL.md",
            context=_context(volume),
            policy=SkillInstallPolicy(url_install_enabled=True),
            name="oversize",
        )


def test_install_skill_from_url_force_bypasses_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = _markdown("warn-alpha") + "\napi_key: leaked\n"

    monkeypatch.setattr(
        "fleet_rlm.skills.install.fetch_url_text",
        lambda url, policy, max_bytes=None: markdown,
    )

    result = install_skill_from_url(
        url="https://example.com/SKILL.md",
        context=_context(volume),
        policy=SkillInstallPolicy(url_install_enabled=True),
        force=True,
    )
    assert result.skill_name == "warn-alpha"
