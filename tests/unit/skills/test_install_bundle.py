from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fleet_rlm.skills.bundle_manifest import SkillBundleManifest, parse_bundle_manifest, verify_bundle_files
from fleet_rlm.skills.errors import SkillInstallDeniedError, SkillValidationError
from fleet_rlm.skills.install import install_skill_from_manifest
from fleet_rlm.skills.schemas import SkillInstallPolicy, SkillWriteContext


def _context(volume: Path) -> SkillWriteContext:
    return SkillWriteContext(volume_mount_path=str(volume), user_id="user-1")


def _markdown(name: str) -> bytes:
    return f'---\nname: {name}\ndescription: "Bundle skill"\n---\n\n# {name}\n'.encode("utf-8")


def test_verify_bundle_files_rejects_hash_mismatch() -> None:
    files = {"SKILL.md": _markdown("bundle-alpha")}
    manifest = SkillBundleManifest(
        name="bundle-alpha",
        files={"SKILL.md": "0" * 64},
    )
    with pytest.raises(SkillValidationError):
        verify_bundle_files(manifest, files)


def test_install_skill_from_manifest_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(SkillValidationError):
        parse_bundle_manifest(
            {
                "name": "bundle-alpha",
                "files": {
                    "SKILL.md": hashlib.sha256(_markdown("bundle-alpha")).hexdigest(),
                    "../secret.txt": hashlib.sha256(b"nope").hexdigest(),
                },
            }
        )


def test_install_skill_from_manifest_commits_directory_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    files = {
        "SKILL.md": _markdown("bundle-alpha"),
        "references/note.md": b"note",
    }
    manifest = SkillBundleManifest(
        name="bundle-alpha",
        files={path: hashlib.sha256(payload).hexdigest() for path, payload in files.items()},
    )
    result = install_skill_from_manifest(
        manifest=manifest,
        files=files,
        context=_context(volume),
        policy=SkillInstallPolicy(bundle_install_enabled=True),
    )
    assert result.skill_name == "bundle-alpha"
    assert (volume / "skills" / "user" / "bundle-alpha" / "references" / "note.md").is_file()


def test_install_skill_from_manifest_disabled_by_policy(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    files = {"SKILL.md": _markdown("bundle-alpha")}
    manifest = SkillBundleManifest(
        name="bundle-alpha",
        files={"SKILL.md": hashlib.sha256(files["SKILL.md"]).hexdigest()},
    )
    with pytest.raises(SkillInstallDeniedError):
        install_skill_from_manifest(
            manifest=manifest,
            files=files,
            context=_context(volume),
            policy=SkillInstallPolicy(bundle_install_enabled=False),
        )
