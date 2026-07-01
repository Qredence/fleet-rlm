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


def test_skill_selection_config_caps_tokens_and_disables_qwen_thinking() -> None:
    import dspy

    base = dspy.LM(
        "qwen3.7-max",
        api_key="test-key",
        api_base="http://localhost:11434",
        custom_llm_provider="openai",
        max_tokens=65536,
        timeout=60.0,
    )

    module = SkillSelectionModule(lm=base)
    base_lm, config = module._get_skill_selection_config()

    assert base_lm is base
    assert config["max_tokens"] == 512
    assert config["temperature"] == 0.0
    assert config["timeout"] == 30.0
    # qwen thinking auto-off via extra_body
    assert config.get("extra_body") == {"enable_thinking": False}


def test_skill_selection_config_returns_empty_overrides_for_none_base() -> None:
    from fleet_rlm.runtime.config import build_lm_config

    assert build_lm_config(None, max_tokens=512, temperature=0.0, timeout=30.0) == {}


def test_skill_selection_module_resolves_delegate_lm_into_config() -> None:
    import dspy

    base = dspy.LM(
        "qwen3.7-max",
        api_key="k",
        max_tokens=65536,
    )

    module = SkillSelectionModule(lm=base)

    assert module._select_lm is base
    base_lm, config = module._get_skill_selection_config()
    assert base_lm is base
    assert config["max_tokens"] == 512


def test_skill_selection_invoke_select_passes_config_overrides() -> None:
    module = SkillSelectionModule()

    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        seen["config"] = kwargs.get("config")
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select
    raw_delegate = MagicMock(name="raw_delegate")
    module._select_lm = raw_delegate

    module._invoke_select(context="ctx", available_skills="- a: b")

    # The delegate is bound as the active LM, and overrides are passed to config.
    assert seen["lm"] is raw_delegate
    assert seen["config"]["max_tokens"] == 512


def test_skill_selection_invoke_select_skips_context_when_no_lm(monkeypatch) -> None:
    module = SkillSelectionModule()

    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        seen["config"] = kwargs.get("config")
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select
    # No delegate LM resolvable at all → _invoke_select must fall back to the
    # global dspy LM without overriding the context.
    monkeypatch.setattr(module, "_get_skill_selection_config", lambda: (None, {"max_tokens": 512}))

    module._invoke_select(context="ctx", available_skills="- a: b")

    # No LM resolvable → falls back to the global dspy LM (no override), but config overrides are still passed.
    assert seen["lm"] is getattr(dspy.settings, "lm", None)
    assert seen["config"] == {"max_tokens": 512}


def test_get_skill_selection_config_self_resolves_when_no_wired_lm(monkeypatch) -> None:
    """Regression: ``AgentRuntime`` does not forward the delegate LM to
    ``SkillSelectionModule`` (``self._select_lm`` is ``None`` in production), so
    ``_get_skill_selection_config`` must self-resolve from env.
    """
    import dspy

    from fleet_rlm.runtime import config as rt_config

    module = SkillSelectionModule()  # lm=None — the regression condition
    assert module._select_lm is None

    fake_base = dspy.LM(
        "gemini-3.1-flash-lite",
        api_key="k",
        api_base="http://x",
        max_tokens=8192,
    )

    monkeypatch.setattr(rt_config, "get_delegate_small_lm_from_env", lambda: fake_base)
    monkeypatch.setattr(rt_config, "get_delegate_lm_from_env", lambda: None)

    base_lm, config = module._get_skill_selection_config()

    assert base_lm is fake_base
    assert config["max_tokens"] == 512


def test_invoke_select_self_resolves_and_binds_lm_context(monkeypatch) -> None:
    """When no LM is wired in, ``_invoke_select`` self-resolves the LM and binds it via ``dspy.settings.context``."""
    import dspy

    from fleet_rlm.runtime import config as rt_config

    module = SkillSelectionModule()
    seen: dict = {}

    def _record_select(**kwargs):
        seen["lm"] = getattr(dspy.settings, "lm", None)
        seen["config"] = kwargs.get("config")
        return dspy.Prediction(skills=["diagnostics"], reasoning="ok")

    module.select = _record_select

    fake_base = dspy.LM(
        "gemini-3.1-flash-lite",
        api_key="k",
        api_base="http://x",
        max_tokens=8192,
    )
    monkeypatch.setattr(rt_config, "get_delegate_small_lm_from_env", lambda: fake_base)
    monkeypatch.setattr(rt_config, "get_delegate_lm_from_env", lambda: None)

    module._invoke_select(context="ctx", available_skills="- a: b")

    assert seen["lm"] is fake_base
    assert seen["config"]["max_tokens"] == 512
