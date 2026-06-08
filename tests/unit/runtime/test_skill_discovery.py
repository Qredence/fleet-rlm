from __future__ import annotations

from fleet_rlm.runtime.modules.skill_selection import (
    AVAILABLE_SKILLS,
    preview_skills_for_turn,
)
from fleet_rlm.runtime.tools.skill_tools import clear_skill_cache, discover_scaffold_skills


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
