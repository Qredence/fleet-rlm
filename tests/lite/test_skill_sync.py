from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from fleet_rlm.skills import SkillManager


def _skill(name: str, instructions: str = "Instructions.") -> bytes:
    return f"---\nname: {name}\ndescription: test skill\n---\n\n{instructions}\n".encode()


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

        def write_workspace_file(self, relative_path: str, content: bytes) -> None:
            self.writes.append((relative_path, content))

    sandbox = RecordingSandbox()
    with pytest.raises(ValueError, match=r"skills/ledger-audit/existing.txt"):
        SkillManager(sandbox, skills).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert sandbox.writes == []
    sandbox.existing["skills/ledger-audit/existing.txt"] = hashlib.sha256(b"local version").hexdigest()
    assert SkillManager(sandbox, skills).sync_local_skills_to_volume() == 1  # type: ignore[arg-type]
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
