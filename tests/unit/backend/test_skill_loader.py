"""Bundled skill markdown loader for fleet_rlm."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.loader import (
    bundled_skills_root,
    load_skill_directory,
    parse_skill_markdown,
    seed_bundled_skills,
    stable_skill_id,
)
from fleet_rlm.skills.models import SkillResource
from fleet_rlm.skills.registry import InMemorySkillRegistry


def test_parse_skill_markdown_frontmatter() -> None:
    text = """---
name: demo
description: "Hello world skill"
disable-model-invocation: true
---

# Body

Do the thing.
"""
    meta, body = parse_skill_markdown(text)
    assert meta["name"] == "demo"
    assert meta["description"] == "Hello world skill"
    assert meta["disable-model-invocation"] is True
    assert body.startswith("# Body")


def test_parse_skill_markdown_supports_multiline_yaml() -> None:
    meta, body = parse_skill_markdown(
        "---\nname: demo\ndescription: >-\n  First line\n  second line\nmetadata:\n  version: 2.0.0\n---\nBody\n"
    )
    assert meta["description"] == "First line second line"
    assert meta["metadata"] == {"version": "2.0.0"}
    assert body == "Body"


def test_parse_skill_markdown_requires_frontmatter() -> None:
    with pytest.raises(SkillValidationError):
        parse_skill_markdown("# no frontmatter\n")


def test_stable_skill_id_is_deterministic() -> None:
    assert stable_skill_id("rlm") == stable_skill_id("rlm")
    assert stable_skill_id("rlm") != stable_skill_id("long-context")


def test_seed_bundled_skills_registers_fleet_catalog() -> None:
    root = bundled_skills_root()
    assert root.is_dir()
    registry = InMemorySkillRegistry()
    created = seed_bundled_skills(registry)
    names = {r.name for r in registry.list_records()}
    assert names == {"long-context", "workspace-files"}
    assert len(created) == len(names)

    # Re-seed is idempotent
    again = seed_bundled_skills(registry)
    assert again == ()

    long_context = next(r for r in registry.list_records() if r.name == "long-context")
    assert long_context.id == stable_skill_id("long-context")
    assert long_context.version == "2.0.0"


def test_load_skill_directory_collects_references(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\n---\n\n# Sample body\n",
        encoding="utf-8",
    )
    refs = skill / "references"
    refs.mkdir()
    (refs / "note.md").write_text("# note\n", encoding="utf-8")
    kwargs = load_skill_directory(skill)
    assert kwargs["name"] == "sample"
    resources = kwargs["resources"]
    assert all(isinstance(resource, SkillResource) for resource in resources)
    assert [resource.path for resource in resources] == ["references/note.md"]
    assert resources[0].body == b"# note\n"


def test_load_skill_directory_rejects_name_mismatch(tmp_path: Path) -> None:
    skill = tmp_path / "actual-name"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: other-name\ndescription: Sample\n---\nBody\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillValidationError, match="match"):
        load_skill_directory(skill)


def test_load_skill_directory_filters_resources_and_reads_binary_assets(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\nlicense: Apache-2.0\ncompatibility: Requires Python 3.12\nmetadata:\n  version: 2.0.0\nallowed-tools: Read Write\ndisable-model-invocation: true\n---\nBody\n",
        encoding="utf-8",
    )
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill / "scripts" / "ignored.sh").write_text("exit 0\n", encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references" / "config.yaml").write_text("ok: true\n", encoding="utf-8")
    (skill / "assets").mkdir()
    (skill / "assets" / "pixel.png").write_bytes(b"\x89PNG\r\n")
    (skill / "assets" / "ignored.bin").write_bytes(b"secret")

    kwargs = load_skill_directory(skill)
    by_path = {resource.path: resource for resource in kwargs["resources"]}
    assert set(by_path) == {"scripts/run.py", "references/config.yaml", "assets/pixel.png"}
    assert by_path["scripts/run.py"].descriptor.encoding == "utf-8"
    assert by_path["assets/pixel.png"].descriptor.encoding == "base64"
    assert kwargs["version"] == "2.0.0"
    assert kwargs["allowed_tools"] == ("Read", "Write")
    assert kwargs["license"] == "Apache-2.0"
    assert kwargs["compatibility"] == "Requires Python 3.12"
    assert kwargs["visibility"] == "hidden"


def test_load_skill_directory_rejects_symlinked_skill_md(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("---\nname: sample\ndescription: Sample\n---\nBody\n", encoding="utf-8")
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").symlink_to(source)
    with pytest.raises(SkillValidationError, match="symlink"):
        load_skill_directory(skill)


def test_load_skill_directory_rejects_individual_resource_over_64_kib(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\n---\nBody\n",
        encoding="utf-8",
    )
    (skill / "references").mkdir()
    (skill / "references" / "huge.txt").write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(SkillValidationError, match="resource exceeds"):
        load_skill_directory(skill)


def test_load_skill_directory_rejects_resource_symlinks(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\n---\nBody\n",
        encoding="utf-8",
    )
    source = tmp_path / "source.txt"
    source.write_text("outside", encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references" / "linked.txt").symlink_to(source)
    with pytest.raises(SkillValidationError, match="symlink"):
        load_skill_directory(skill)


def test_load_skill_directory_excludes_executable_allowed_resource(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\n---\nBody\n",
        encoding="utf-8",
    )
    (skill / "scripts").mkdir()
    script = skill / "scripts" / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    script.chmod(0o755)
    assert load_skill_directory(skill)["resources"] == ()


def test_total_bound_counts_unsupported_files(tmp_path: Path) -> None:
    skill = tmp_path / "sample"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample\n---\nBody\n",
        encoding="utf-8",
    )
    (skill / "assets").mkdir()
    (skill / "assets" / "unsupported.bin").write_bytes(b"x" * (256 * 1024))
    with pytest.raises(SkillValidationError, match="directory exceeds"):
        load_skill_directory(skill)


def test_create_app_seeds_visible_skills_without_instruction_leak() -> None:
    app = create_testing_app()
    user = uuid4()
    workspace = uuid4()
    headers = {
        "X-Fleet-User-Id": str(user),
        "X-Fleet-Workspace-Id": str(workspace),
    }
    client = TestClient(app)
    response = client.get("/api/skills", headers=headers)
    assert response.status_code == 200
    body = response.json()
    names = {item["name"] for item in body}
    assert names == {"long-context", "workspace-files"}
    dumped = str(body)
    assert "instructions" not in dumped
    for item in body:
        assert "instructions" not in item
        # Full skill bodies must not appear on cards
        assert "SUBMIT(answer=" not in str(item)


def test_catalog_seed_failure_leaves_skill_discovery_empty(monkeypatch) -> None:
    def unavailable(_registry):
        raise OSError("catalog unavailable")

    monkeypatch.setattr("fleet_rlm.skills.loader.seed_bundled_skills", unavailable)
    app = create_testing_app()
    headers = {
        "X-Fleet-User-Id": str(uuid4()),
        "X-Fleet-Workspace-Id": str(uuid4()),
    }

    response = TestClient(app).get("/api/skills", headers=headers)

    assert response.status_code == 200
    assert response.json() == []
