from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.skills.schemas import SkillRuntimeContext, SkillVisibilityPolicy
from fleet_rlm.skills.selection import SkillSelectionModule


def _write_directory_skill(volume: Path, name: str, description: str) -> None:
    skill_dir = volume / "skills" / "user" / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{description}"\n---\n\n# {name}\n',
        encoding="utf-8",
    )


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


def test_catalog_backed_auto_selection_discovers_visible_directory_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))
    context = SkillRuntimeContext(volume_mount_path=str(volume))

    result = module(user_request="Use zephyr alpha routing", context=context)

    module.select.assert_not_called()
    assert result.selected_skills == ["alpha-route"]
    assert result.active_skills.instructions["alpha-route"].startswith("---")


def test_catalog_backed_auto_selection_excludes_invisible_directory_skill(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "hidden-route", "Obsidian hidden routing support.")
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(excluded_skill_ids=["hidden-route"]),
    )

    result = module(user_request="Use obsidian hidden routing", context=context)

    module.select.assert_not_called()
    assert result.selected_skills == []


def test_explicit_ids_win_over_catalog_backed_auto_candidates(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    _write_directory_skill(volume, "manual-route", "Manual explicit support.")
    module = SkillSelectionModule(max_skills=2)
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        selected_skill_ids=["manual-route"],
    )

    result = module(
        user_request="Use zephyr alpha routing",
        context=context,
        selected_skill_ids=["manual-route"],
    )

    module.select.assert_not_called()
    assert result.selected_skills == ["manual-route", "alpha-route"]


def test_scaffold_skills_still_auto_select_with_context() -> None:
    module = SkillSelectionModule()
    module.select = MagicMock(side_effect=AssertionError("selector should not be called"))

    result = module(
        user_request="Use playwright to inspect this javascript page",
        context=SkillRuntimeContext(),
    )

    module.select.assert_not_called()
    assert result.selected_skills == ["browser-interaction"]


def test_bounded_selector_sees_only_visible_catalog_candidates(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    _write_directory_skill(volume, "beta-route", "Zephyr beta routing support.")
    _write_directory_skill(volume, "hidden-route", "Zephyr hidden routing support.")
    module = SkillSelectionModule(max_skills=1)
    module.select = MagicMock(return_value=dspy.Prediction(skills=["beta-route"], reasoning="beta fits"))
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(included_skill_ids=["alpha-route", "beta-route"]),
    )

    result = module(user_request="Use zephyr routing", context=context)

    module.select.assert_called_once()
    available_skills = module.select.call_args.kwargs["available_skills"]
    assert "alpha-route" in available_skills
    assert "beta-route" in available_skills
    assert "hidden-route" not in available_skills
    assert "diagnostics" not in available_skills
    assert result.selected_skills == ["beta-route"]


def test_catalog_backed_deterministic_fallback_when_selector_fails(tmp_path: Path) -> None:
    volume = tmp_path / "memory"
    _write_directory_skill(volume, "alpha-route", "Zephyr alpha routing support.")
    _write_directory_skill(volume, "beta-route", "Zephyr beta routing support.")
    module = SkillSelectionModule(max_skills=1)
    module.select = MagicMock(side_effect=RuntimeError("selector unavailable"))
    context = SkillRuntimeContext(
        volume_mount_path=str(volume),
        visibility=SkillVisibilityPolicy(included_skill_ids=["alpha-route", "beta-route"]),
    )

    result = module(user_request="Use zephyr routing", context=context)

    module.select.assert_called_once()
    assert result.selected_skills == ["alpha-route"]
    assert result.reasoning == ""
