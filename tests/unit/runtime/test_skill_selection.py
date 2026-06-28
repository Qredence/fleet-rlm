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


def test_skill_selection_invoke_select_skips_context_when_no_lm(monkeypatch) -> None:
    module = SkillSelectionModule()

    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select
    # No bounded LM resolvable at all → _invoke_select must fall back to the
    # global dspy LM without overriding the context.
    monkeypatch.setattr(module, "_get_skill_selection_lm", lambda: None)

    module._invoke_select(context="ctx", available_skills="- a: b")

    # No LM resolvable → falls back to the global dspy LM (no override).
    assert seen["lm"] is getattr(dspy.settings, "lm", None)


def test_get_skill_selection_lm_self_resolves_when_no_wired_lm(monkeypatch) -> None:
    """Regression: ``AgentRuntime`` does not forward the delegate LM to
    ``SkillSelectionModule`` (``self._select_lm`` is ``None`` in production), so
    ``_get_skill_selection_lm`` must self-resolve from env and bound it —
    otherwise skill selection lands on the unbounded planner LM (qwen3.7-plus,
    thinking ON) for 18-26s/turn (tr-0dc96586 / tr-5671ce47).
    """
    from fleet_rlm.runtime import config as rt_config
    from fleet_rlm.runtime.lm import BoundedChatLM

    module = SkillSelectionModule()  # lm=None — the regression condition
    assert module._select_lm is None

    fake_base = MagicMock(name="delegate_base")
    fake_base.model = "gemini-3.1-flash-lite"
    fake_base.kwargs = {"api_key": "k", "api_base": "http://x", "max_tokens": 8192}

    monkeypatch.setattr(rt_config, "get_delegate_small_lm_from_env", lambda: fake_base)
    monkeypatch.setattr(rt_config, "get_delegate_lm_from_env", lambda: None)

    resolved = module._get_skill_selection_lm()

    assert isinstance(resolved, BoundedChatLM), "expected self-resolved bounded LM, fell back to raw/global instead"


def test_invoke_select_self_resolves_and_binds_bounded_lm(monkeypatch) -> None:
    """When no LM is wired in, ``_invoke_select`` self-resolves a bounded LM
    and binds it via ``dspy.settings.context`` (not the global planner LM)."""
    from fleet_rlm.runtime import config as rt_config
    from fleet_rlm.runtime.lm import BoundedChatLM

    module = SkillSelectionModule()
    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select

    fake_base = MagicMock(name="delegate_base")
    fake_base.model = "gemini-3.1-flash-lite"
    fake_base.kwargs = {"api_key": "k", "api_base": "http://x", "max_tokens": 8192}
    monkeypatch.setattr(rt_config, "get_delegate_small_lm_from_env", lambda: fake_base)
    monkeypatch.setattr(rt_config, "get_delegate_lm_from_env", lambda: None)

    module._invoke_select(context="ctx", available_skills="- a: b")

    assert isinstance(seen["lm"], BoundedChatLM), f"expected bounded LM bound during select, got {seen.get('lm')!r}"
