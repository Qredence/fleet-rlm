"""Bundled skill markdown loader for fleet_rlm."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.loader import (
    bundled_skills_root,
    load_skill_directory,
    parse_skill_markdown,
    seed_bundled_skills,
    stable_skill_id,
)
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
    assert "rlm" in names
    assert "long-context" in names
    assert "sandbox-execution" in names
    assert "volume-bootstrap" in names
    assert "diagnostics" in names
    assert "browser-interaction" in names
    assert "writing-great-skills" in names
    # Live-only skills must not be present
    assert "delegation" not in names
    assert "optimization" not in names
    assert "dspy-programs" not in names
    assert len(created) == len(names)

    # Re-seed is idempotent
    again = seed_bundled_skills(registry)
    assert again == ()

    writing = next(r for r in registry.list_records() if r.name == "writing-great-skills")
    assert writing.visibility == "hidden"
    assert "Predictability" in writing.instructions or "predictability" in writing.instructions.lower()

    rlm = next(r for r in registry.list_records() if r.name == "rlm")
    assert rlm.id == stable_skill_id("rlm")
    assert rlm.resources_available is True
    assert any(path.endswith("architecture.md") for path in rlm.resources)


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
    assert "references/note.md" in kwargs["resource_bodies"]


def test_create_app_seeds_visible_skills_without_instruction_leak() -> None:
    app = create_app()
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
    assert "rlm" in names
    assert "writing-great-skills" not in names  # hidden / user-invoked
    dumped = str(body)
    assert "instructions" not in dumped
    for item in body:
        assert "instructions" not in item
        # Full skill bodies must not appear on cards
        assert "SUBMIT(answer=" not in str(item)
