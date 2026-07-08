from __future__ import annotations

import json

from fleet_rlm.skills.active import ActiveSkills
from fleet_rlm.skills.schemas import SkillResource, SkillResourceKind


def test_active_skills_defaults_resources_and_sandbox_paths() -> None:
    skills = ActiveSkills()
    assert skills.resources == {}
    assert skills.sandbox_paths == {}


def test_active_skills_to_sandbox_includes_new_keys() -> None:
    skills = ActiveSkills(
        selected=["long-context"],
        catalog={"long-context": "Process large context"},
        instructions={"long-context": "SECRET FULL MARKDOWN"},
        sources={"long-context": "scaffold:long-context"},
        resources={"long-context": [SkillResource(kind=SkillResourceKind.REFERENCE, path="references/chunking.md")]},
        sandbox_paths={"long-context": "/home/daytona/memory/skills/system/long-context"},
    )
    payload = json.loads(skills.to_sandbox().decode("utf-8"))
    assert payload["selected"] == ["long-context"]
    assert payload["instructions"]["long-context"] == "SECRET FULL MARKDOWN"
    assert payload["resources"]["long-context"][0]["path"] == "references/chunking.md"
    assert payload["sandbox_paths"]["long-context"].endswith("long-context")


def test_active_skills_preview_unchanged_without_resource_leakage() -> None:
    skills = ActiveSkills(
        selected=["long-context"],
        catalog={"long-context": "Process large context"},
        instructions={"long-context": "SECRET FULL MARKDOWN"},
        sources={"long-context": "scaffold:long-context"},
        resources={"long-context": [SkillResource(kind=SkillResourceKind.REFERENCE, path="references/chunking.md")]},
    )
    preview = skills.rlm_preview()
    assert "long-context" in preview
    assert "Process large context" in preview
    assert "SECRET FULL MARKDOWN" not in preview
    assert "chunking.md" not in preview
