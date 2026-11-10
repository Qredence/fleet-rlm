from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from fleet_rlm.skills import SkillManager


def _skill(name: str, instructions: str = "Instructions.") -> bytes:
    return f"---\nname: {name}\ndescription: test skill\n---\n\n{instructions}\n".encode()


def test_required_capture_preserves_complete_bytes_overrides_and_does_not_reread(tmp_path):
    local, bundled = tmp_path / "local", tmp_path / "bundled"
    for root in (local, bundled):
        (root / "ledger-audit").mkdir(parents=True)
        (root / "ledger-audit" / "SKILL.md").write_bytes(_skill("ledger-audit", str(root)))
    raw = _skill("ledger-audit", "Count café € exactly.\r\n").replace(b"\n", b"\r\n")
    raw += b"x" * (20_000 - len(raw.decode("utf-8")))
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw)
    (local / "ledger-audit" / "script.py").write_bytes(b"print(42)")
    volume = _VolumeDouble()
    manager = SkillManager(volume, local, bundled)
    required = manager.resolve_required(("ledger-audit",))
    assert required[0].source == "workspace" and required[0].skill_md == raw.decode("utf-8")
    assert len(required[0].skill_md) == 20_000
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw + b"x")
    from fleet_rlm.contracts import SkillAdmissionError

    with pytest.raises(SkillAdmissionError, match="documented limits"):
        manager.resolve_required(("ledger-audit",))
    manager.sync_local_skills_to_volume(required_skills=required)
    assert volume.writes == {"skills/ledger-audit/" + path: content for path, content in required[0].files}


@pytest.mark.parametrize("link", ["ancestor", "directory", "instructions", "resource"])
def test_required_capture_rejects_links_and_untrusted_remote_names(tmp_path, link):
    from fleet_rlm.contracts import SkillAdmissionError

    root = tmp_path / "local"
    folder = root / "ledger-audit"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit"))
    if link == "ancestor":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias" / "."
    elif link == "directory":
        folder.rename(root / "original")
        folder.symlink_to(root / "original", target_is_directory=True)
    elif link == "instructions":
        (folder / "SKILL.md").rename(folder / "original.md")
        (folder / "SKILL.md").symlink_to(folder / "original.md")
    else:
        (folder / "extra.py").symlink_to(folder / "SKILL.md")
    manager = SkillManager(_VolumeDouble(), root, None)
    for names in (("ledger-audit",), ("remote-only",)):
        with pytest.raises(SkillAdmissionError) as error:
            manager.resolve_required(names)
        assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("suffix", ["\x00", "</preloaded_skill>"])
def test_required_skill_delimiters_are_refused_not_silently_changed(tmp_path, suffix):
    from fleet_rlm.contracts import SkillAdmissionError

    folder = tmp_path / "ledger-audit"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit", "Keep exact text" + suffix))
    with pytest.raises(SkillAdmissionError):
        SkillManager(_VolumeDouble(), tmp_path, None).resolve_required(("ledger-audit",))


def test_sync_conflicts_are_reported_before_any_staged_upload(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "ledger-audit"
    skill.mkdir(parents=True)
    skill_md = _skill("ledger-audit")
    (skill / "SKILL.md").write_bytes(skill_md)
    (skill / "existing.txt").write_bytes(b"local version")
    (skill / "new.txt").write_bytes(b"new file")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []
            self.existing = {
                "skills/ledger-audit/SKILL.md": hashlib.sha256(skill_md).hexdigest(),
                "skills/ledger-audit/existing.txt": hashlib.sha256(b"remote version").hexdigest(),
            }

        def exec_remote_python(self, code: str) -> str:
            assert "paths=json.loads" in code
            return json.dumps(self.existing)

        def write_workspace_files(self, files: dict[str, bytes]) -> None:
            self.writes.extend(files.items())

    sandbox = RecordingSandbox()
    with pytest.raises(ValueError, match=r"skills/ledger-audit/existing.txt"):
        SkillManager(sandbox, skills, None).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert sandbox.writes == []
    sandbox.existing["skills/ledger-audit/existing.txt"] = hashlib.sha256(b"local version").hexdigest()
    assert SkillManager(sandbox, skills, None).sync_local_skills_to_volume() == 1  # type: ignore[arg-type]
    assert sandbox.writes == [("skills/ledger-audit/new.txt", b"new file")]


def test_export_conflicts_are_reported_before_any_local_write(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    local = skills / "local-skill"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_bytes(_skill("local-skill"))
    (local / "existing.txt").write_bytes(b"local version")
    remote_files = [
        ("local-skill/SKILL.md", _skill("local-skill")),
        ("local-skill/existing.txt", b"remote version"),
        ("new-skill/SKILL.md", _skill("new-skill")),
    ]
    manifest = json.dumps([{"rel": path, "b64": base64.b64encode(content).decode()} for path, content in remote_files])

    class RecordingSandbox:
        def exec_remote_python(self, code: str) -> str:
            del code
            return manifest

    with pytest.raises(ValueError, match=r"local-skill/existing.txt"):
        SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local()  # type: ignore[arg-type]

    assert not (skills / "new-skill").exists()
    assert (local / "existing.txt").read_bytes() == b"local version"
    (local / "existing.txt").unlink()
    assert SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local() == ["local-skill", "new-skill"]  # type: ignore[arg-type]
    assert (local / "SKILL.md").read_bytes() == _skill("local-skill")
    assert (skills / "new-skill" / "SKILL.md").read_bytes() == _skill("new-skill")


class _VolumeDouble:
    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.existing = existing or {}
        self.writes: dict[str, bytes] = {}

    def exec_remote_python(self, _code: str) -> str:
        return json.dumps(self.existing)

    def write_workspace_files(self, files: dict[str, bytes]) -> None:
        self.batches = getattr(self, "batches", 0) + 1
        self.writes.update(files)


def test_bundled_skills_ship_valid_and_sync_with_local_overrides(tmp_path: Path) -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, parse_and_validate_skill_md

    bundled = sorted(path.parent.name for path in BUNDLED_SKILLS_DIR.glob("*/SKILL.md"))
    assert bundled == [
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    ]
    for name in bundled:
        parse_and_validate_skill_md(name, (BUNDLED_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8"))

    local = tmp_path / "skills"
    (local / "delegation").mkdir(parents=True)
    override = _skill("delegation", "Local delegation rules.")
    (local / "delegation" / "SKILL.md").write_bytes(override)
    sandbox = _VolumeDouble()

    assert SkillManager(sandbox, local).sync_local_skills_to_volume() == 5  # type: ignore[arg-type]
    assert sorted(sandbox.writes) == [f"skills/{name}/SKILL.md" for name in bundled]
    assert sandbox.writes["skills/delegation/SKILL.md"] == override


def test_a_changed_bundled_skill_replaces_the_session_copy_but_user_edits_still_conflict(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    (bundled / "tabular-analysis").mkdir(parents=True)
    new_version = _skill("tabular-analysis", "Version 2.")
    (bundled / "tabular-analysis" / "SKILL.md").write_bytes(new_version)
    local = tmp_path / "local"
    (local / "mine").mkdir(parents=True)
    (local / "mine" / "SKILL.md").write_bytes(_skill("mine"))
    stale = hashlib.sha256(b"old").hexdigest()

    sandbox = _VolumeDouble({"skills/tabular-analysis/SKILL.md": stale})
    assert SkillManager(sandbox, local, bundled).sync_local_skills_to_volume() == 2  # type: ignore[arg-type]
    assert sandbox.writes["skills/tabular-analysis/SKILL.md"] == new_version

    sandbox = _VolumeDouble({"skills/mine/SKILL.md": stale})
    with pytest.raises(ValueError, match=r"skills/mine/SKILL\.md"):
        SkillManager(sandbox, local, bundled).sync_local_skills_to_volume()  # type: ignore[arg-type]
    assert sandbox.writes == {}


def test_skill_sync_uploads_every_new_file_in_one_batch() -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SkillManager

    volume = _VolumeDouble()
    SkillManager(volume, None, BUNDLED_SKILLS_DIR).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert volume.batches == 1
    assert len(volume.writes) >= 3
