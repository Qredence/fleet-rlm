from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fleet_rlm.skills.provenance import write_provenance
from fleet_rlm.skills.schemas import (
    SkillInstallPolicy,
    SkillInstallSource,
    SkillProvenanceRecord,
    SkillScope,
    SkillTrustLevel,
    SkillWriteContext,
)
from fleet_rlm.skills.update import check_skill_update, update_installed_skill


def _markdown(name: str) -> str:
    return f'---\nname: {name}\ndescription: "Remote skill"\n---\n\n# {name}\n'


def _context(volume: Path) -> SkillWriteContext:
    return SkillWriteContext(volume_mount_path=str(volume), user_id="user-1")


def test_check_skill_update_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    skill_dir = volume / "skills" / "user" / "remote-alpha"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(_markdown("remote-alpha"), encoding="utf-8")
    now = datetime.now(UTC).isoformat()
    write_provenance(
        str(volume),
        SkillProvenanceRecord(
            skill_name="remote-alpha",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash="local-hash",
            upstream_content_hash="old-upstream",
            installed_at=now,
        ),
    )

    monkeypatch.setattr(
        "fleet_rlm.skills.update.fetch_url_bytes",
        lambda url, policy: _markdown("remote-alpha-updated").encode("utf-8"),
    )

    status = check_skill_update(
        skill_name="remote-alpha",
        scope=SkillScope.USER,
        context=_context(volume),
        policy=SkillInstallPolicy(url_install_enabled=True),
    )
    assert status.drift_detected is True


def test_update_installed_skill_noop_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = _markdown("remote-alpha")
    skill_dir = volume / "skills" / "user" / "remote-alpha"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(markdown, encoding="utf-8")
    from fleet_rlm.skills.provenance import content_hash_for_markdown

    content_hash = content_hash_for_markdown(markdown)
    now = datetime.now(UTC).isoformat()
    write_provenance(
        str(volume),
        SkillProvenanceRecord(
            skill_name="remote-alpha",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash=content_hash,
            upstream_content_hash=content_hash,
            installed_at=now,
        ),
    )

    monkeypatch.setattr(
        "fleet_rlm.skills.update.fetch_url_bytes",
        lambda url, policy: markdown.encode("utf-8"),
    )

    outcome = update_installed_skill(
        skill_name="remote-alpha",
        scope=SkillScope.USER,
        context=_context(volume),
        policy=SkillInstallPolicy(url_install_enabled=True),
    )
    from fleet_rlm.skills.schemas import SkillUpdateStatus

    assert isinstance(outcome, SkillUpdateStatus)
    assert outcome.drift_detected is False


def test_update_installed_skill_applies_url_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "memory"
    markdown = _markdown("remote-alpha")
    skill_dir = volume / "skills" / "user" / "remote-alpha"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(markdown, encoding="utf-8")
    now = datetime.now(UTC).isoformat()
    write_provenance(
        str(volume),
        SkillProvenanceRecord(
            skill_name="remote-alpha",
            scope=SkillScope.USER,
            source=SkillInstallSource.URL_SINGLE,
            source_url="https://example.com/SKILL.md",
            trust_level=SkillTrustLevel.COMMUNITY,
            content_hash="stale",
            upstream_content_hash="stale",
            installed_at=now,
        ),
    )

    updated_markdown = _markdown("remote-alpha") + "\nUpdated content.\n"

    monkeypatch.setattr(
        "fleet_rlm.skills.update.fetch_url_bytes",
        lambda url, policy: updated_markdown.encode("utf-8"),
    )
    monkeypatch.setattr(
        "fleet_rlm.skills.install.fetch_url_text",
        lambda url, policy, max_bytes=None: updated_markdown,
    )

    outcome = update_installed_skill(
        skill_name="remote-alpha",
        scope=SkillScope.USER,
        context=_context(volume),
        policy=SkillInstallPolicy(url_install_enabled=True),
    )
    assert outcome.committed is True
    assert "Updated content." in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
