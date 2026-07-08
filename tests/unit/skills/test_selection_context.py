from __future__ import annotations

import logging
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy
from fleet_rlm.skills.selection import SkillSelectionModule


def test_explicit_visible_ids_prioritized_ahead_of_keyword_candidates() -> None:
    module = SkillSelectionModule()
    context = SkillRuntimeContext(selected_skill_ids=["rlm"])

    result = module(
        user_request="Use playwright to inspect this javascript page",
        context=context,
        selected_skill_ids=["rlm"],
    )

    assert result.selected_skills[0] == "rlm"
    assert "browser-interaction" in result.selected_skills


def test_explicit_invisible_ids_are_dropped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    module = SkillSelectionModule()
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["rlm"]),
        selected_skill_ids=["rlm"],
    )

    with caplog.at_level(logging.WARNING):
        result = module(
            user_request="hello",
            context=context,
            selected_skill_ids=["rlm"],
            explicit_only=True,
        )

    assert result.selected_skills == []
    assert any("dropping invisible or unknown skill id 'rlm'" in record.message for record in caplog.records)


def test_omitting_context_preserves_keyword_only_behavior() -> None:
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))

    result = module(user_request="hello there", core_memory="recent context")

    module.select.assert_not_called()
    assert result.selected_skills == []
    assert result.skill_context == ""


def test_explicit_only_skips_keyword_candidates() -> None:
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))
    context = SkillRuntimeContext(selected_skill_ids=["diagnostics"])

    result = module(
        user_request="debug a broken sandbox dspy module",
        context=context,
        selected_skill_ids=["diagnostics"],
        explicit_only=True,
    )

    module.select.assert_not_called()
    assert result.selected_skills == ["diagnostics"]
    assert result.active_skills.instructions["diagnostics"].startswith("---")


def test_bounded_selector_only_sees_visible_candidates() -> None:
    module = SkillSelectionModule(max_skills=1)
    module.select = MagicMock(
        return_value=dspy.Prediction(
            skills=["diagnostics"],
            reasoning="diagnostics is the best fit",
        )
    )
    context = SkillRuntimeContext(
        visibility=SkillVisibilityPolicy(included_skill_ids=["diagnostics", "sandbox-execution"]),
    )

    result = module(
        user_request="debug a broken sandbox dspy module",
        context=context,
    )

    module.select.assert_called_once()
    available_skills = module.select.call_args.kwargs["available_skills"]
    assert "diagnostics" in available_skills
    assert "sandbox-execution" in available_skills
    assert "browser-interaction" not in available_skills
    assert result.selected_skills == ["diagnostics"]
