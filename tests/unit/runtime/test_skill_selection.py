from __future__ import annotations

from unittest.mock import MagicMock

import dspy

from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule


def test_skill_selection_no_keyword_match_skips_llm_selector() -> None:
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))

    result = module(user_request="hello there", core_memory="recent context")

    module.select.assert_not_called()
    assert result.selected_skills == []
    assert result.skill_context == ""
    assert result.reasoning == ""


def test_skill_selection_caps_selected_skills_to_loaded_context() -> None:
    module = SkillSelectionModule(max_skills=1)
    module.select = MagicMock(
        return_value=dspy.Prediction(
            skills=["diagnostics", "sandbox-execution"],
            reasoning="diagnostics is the best fit",
        )
    )
    module._load_skills = MagicMock(return_value="[Skill: diagnostics]\nInstructions")

    result = module(user_request="debug a broken sandbox dspy module")

    module.select.assert_called_once()
    module._load_skills.assert_called_once_with(["diagnostics"])
    assert result.selected_skills == ["diagnostics"]
    assert result.skill_context == "[Skill: diagnostics]\nInstructions"
    assert result.reasoning == "diagnostics is the best fit"


def test_skill_selection_falls_back_when_llm_returns_no_valid_skills() -> None:
    module = SkillSelectionModule(max_skills=2)
    module.select = MagicMock(return_value=dspy.Prediction(skills=["unknown"], reasoning="bad parse"))
    module._load_skills = MagicMock(return_value="[Skill: fallback]\nInstructions")

    result = module(user_request="debug a broken sandbox dspy module")

    module.select.assert_called_once()
    assert result.selected_skills == ["sandbox-execution", "dspy-programs"]
    assert result.reasoning == ""
