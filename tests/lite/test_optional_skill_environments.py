from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SANDBOX_SKILL_HELPERS_CODE

BUNDLED_SKILLS = (
    "delegation",
    "long-context-search",
    "repository-inspection",
    "skill-creator",
    "tabular-analysis",
)


def test_bundled_skill_discovery_and_activation_do_not_install_packages(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "skills"
    shutil.copytree(BUNDLED_SKILLS_DIR, root)
    helpers: dict = {}
    exec(SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})"), helpers)

    def unexpected_process(*_args, **_kwargs):
        pytest.fail("Discovering or activating a Skill must not launch a process")

    monkeypatch.setattr(helpers["subprocess"], "run", unexpected_process)
    catalog = {item["name"] for item in helpers["list_skills"]()}
    assert catalog == set(BUNDLED_SKILLS)
    for name in BUNDLED_SKILLS:
        activated = helpers["use_skill"](name)
        assert f"# Skill: {name}" in activated
