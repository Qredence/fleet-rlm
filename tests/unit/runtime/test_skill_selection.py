from __future__ import annotations

from unittest.mock import MagicMock

import dspy

from fleet_rlm.runtime.modules.skill_selection import SkillSelectionModule
from fleet_rlm.runtime.sandbox_types import ActiveSkills


def test_browser_interaction_skill_is_cataloged_and_keyword_selected() -> None:
    from fleet_rlm.runtime.modules.skill_selection import AVAILABLE_SKILLS

    module = SkillSelectionModule()

    result = module(user_request="Use playwright to inspect this javascript page")

    assert "browser-interaction" in AVAILABLE_SKILLS
    assert result.selected_skills == ["browser-interaction"]
    assert "[Active Skills]" in result.skill_context
    assert "browser-interaction" in result.skill_context
    assert result.active_skills.selected == ["browser-interaction"]
    assert result.active_skills.instructions["browser-interaction"].startswith("---")


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
    module._load_active_skills = MagicMock(
        return_value=ActiveSkills(
            selected=["diagnostics"],
            catalog={"diagnostics": "Debug runtime failures"},
            instructions={"diagnostics": "Instructions"},
            sources={"diagnostics": "scaffold:diagnostics"},
        )
    )

    result = module(user_request="debug a broken sandbox dspy module")

    module.select.assert_called_once()
    module._load_active_skills.assert_called_once_with(["diagnostics"])
    assert result.selected_skills == ["diagnostics"]
    assert "[Active Skills]" in result.skill_context
    assert "diagnostics: Debug runtime failures" in result.skill_context
    assert result.active_skills.instructions["diagnostics"] == "Instructions"
    assert result.reasoning == "diagnostics is the best fit"


def test_skill_selection_falls_back_when_llm_returns_no_valid_skills() -> None:
    module = SkillSelectionModule(max_skills=2)
    module.select = MagicMock(return_value=dspy.Prediction(skills=["unknown"], reasoning="bad parse"))

    result = module(user_request="debug a broken sandbox dspy module")

    module.select.assert_called_once()
    assert len(result.selected_skills) == 2
    assert set(result.selected_skills) <= {"sandbox-execution", "dspy-programs", "diagnostics"}
    assert result.reasoning == ""


def test_skill_selection_parses_stringified_skill_list() -> None:
    module = SkillSelectionModule()

    selected = module._parse_skill_names("['sandbox-execution', 'delegation']")

    assert selected == ["sandbox-execution", "delegation"]


def test_skill_selection_lm_clone_caps_tokens_and_disables_qwen_thinking() -> None:
    from fleet_rlm.runtime.lm import BoundedChatLM
    from fleet_rlm.runtime.modules.skill_selection import _build_skill_selection_lm

    base = MagicMock()
    base.model = "qwen3.7-max"
    base.kwargs = {
        "api_key": "test-key",
        "api_base": "http://localhost:11434",
        "custom_llm_provider": "openai",
        "max_tokens": 65536,
        "timeout": 60.0,
    }

    capped = _build_skill_selection_lm(base)

    assert isinstance(capped, BoundedChatLM)
    assert capped.model == "qwen3.7-max"
    assert capped._max_tokens == 512
    assert capped._temperature == 0.0
    assert capped._timeout == 30.0
    assert capped.num_retries == 0
    assert capped._disable_thinking is True  # qwen thinking auto-off
    assert capped._api_key == "test-key"
    assert capped._api_base == "http://localhost:11434"


def test_skill_selection_lm_clone_returns_none_for_none_base() -> None:
    from fleet_rlm.runtime.modules.skill_selection import _build_skill_selection_lm

    assert _build_skill_selection_lm(None) is None


def test_skill_selection_module_threads_delegate_lm_into_capped_clone() -> None:
    from fleet_rlm.runtime.lm import BoundedChatLM

    base = MagicMock()
    base.model = "qwen3.7-max"
    base.kwargs = {"api_key": "k", "max_tokens": 65536}

    module = SkillSelectionModule(lm=base)

    assert module._select_lm is base
    # The capped clone is a BoundedChatLM built from the delegate LM's creds.
    assert isinstance(module._select_lm_capped, BoundedChatLM)


def test_skill_selection_invoke_select_binds_capped_lm_context() -> None:
    module = SkillSelectionModule()

    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select
    capped = MagicMock(name="capped_lm")
    module._select_lm_capped = capped
    module._select_lm = MagicMock(name="raw_delegate")

    module._invoke_select(context="ctx", available_skills="- a: b")

    # The capped clone is bound as the active LM for the duration of the call.
    assert seen["lm"] is capped


def test_skill_selection_invoke_select_skips_context_when_no_lm() -> None:
    module = SkillSelectionModule()

    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select
    module._select_lm_capped = None
    module._select_lm = None

    module._invoke_select(context="ctx", available_skills="- a: b")

    # No delegate LM configured → falls back to the global dspy LM (no override).
    assert seen["lm"] is getattr(dspy.settings, "lm", None)
