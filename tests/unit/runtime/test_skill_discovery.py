from __future__ import annotations

from pathlib import Path

from fleet_rlm.runtime.modules.skill_selection import (
    AVAILABLE_SKILLS,
    preview_skills_for_turn,
)
from fleet_rlm.runtime.tools.skill_tools import clear_skill_cache, discover_scaffold_skills
from fleet_rlm.skills.schemas import SkillRuntimeContext


def _write_directory_skill(volume: Path, name: str, description: str) -> None:
    skill_dir = volume / "skills" / "user" / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


def test_discover_scaffold_skills_includes_rlm_and_browser_interaction() -> None:
    clear_skill_cache()
    catalog = discover_scaffold_skills()
    assert "rlm" in catalog
    assert "browser-interaction" in catalog
    assert "long-context" in catalog


def test_available_skills_matches_scaffold_discovery() -> None:
    clear_skill_cache()
    assert set(AVAILABLE_SKILLS) == set(discover_scaffold_skills())


def test_preview_skills_for_turn_url_analysis_selects_long_context() -> None:
    skills = preview_skills_for_turn("Summarize https://dspy.ai/diving-deeper/rlm/")
    assert "long-context" in skills


def test_preview_skills_for_turn_playwright_selects_browser() -> None:
    skills = preview_skills_for_turn("Use playwright to inspect this javascript page")
    assert "browser-interaction" in skills


def test_preview_skills_for_turn_routing_hint_adds_long_context() -> None:
    skills = preview_skills_for_turn(
        "What is the exact quote from Chad Gates?",
        routing_decision="large_context_rlm",
    )
    assert "long-context" in skills


def test_preview_skills_for_turn_first_turn_adds_rlm() -> None:
    skills = preview_skills_for_turn("Hello", is_first_turn=True)
    assert "rlm" in skills


def test_preview_skills_for_turn_uses_visible_catalog_context(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")

    skills = preview_skills_for_turn(
        "Use zephyr alpha routing",
        context=SkillRuntimeContext(volume_mount_path=str(volume)),
    )

    assert skills == ["alpha-route"]
