from __future__ import annotations

from typing import Any

from fleet_rlm.cli import runtime_factory


def test_build_chat_agent_for_runtime_mode_defaults_to_modal_builder(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_modal_builder(*, options, planner_lm=None):
        captured["options"] = options
        captured["planner_lm"] = planner_lm
        return "modal-agent"

    monkeypatch.setattr(
        runtime_factory,
        "_build_modal_chat_agent_from_options",
        _fake_modal_builder,
    )

    agent = runtime_factory.build_chat_agent_for_runtime_mode(
        runtime_mode="unexpected-mode",
        react_max_iters=6,
        timeout=45,
        secret_name="modal-secret",
        volume_name="modal-volume",
        planner_lm="planner-lm",
    )

    assert agent == "modal-agent"
    options = captured["options"]
    assert options.react_max_iters == 6
    assert options.timeout == 45
    assert options.secret_name == "modal-secret"
    assert options.volume_name == "modal-volume"
    assert captured["planner_lm"] == "planner-lm"


def test_build_chat_agent_for_runtime_mode_filters_modal_only_setup_for_daytona(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_daytona_builder(*, options, planner_lm=None):
        captured["options"] = options
        captured["planner_lm"] = planner_lm
        return "daytona-agent"

    monkeypatch.setattr(
        runtime_factory,
        "_build_daytona_workbench_chat_agent_from_options",
        _fake_daytona_builder,
    )

    agent = runtime_factory.build_chat_agent_for_runtime_mode(
        runtime_mode="daytona_pilot",
        react_max_iters=5,
        deep_react_max_iters=9,
        enable_adaptive_iters=False,
        rlm_max_iterations=11,
        rlm_max_llm_calls=17,
        max_depth=4,
        timeout=123,
        secret_name="ignored-secret",
        volume_name="ignored-volume",
        history_max_turns=8,
        planner_lm="planner-lm",
        interpreter_async_execute=False,
        guardrail_mode="warn",
        max_output_chars=1200,
        min_substantive_chars=40,
        delegate_lm="delegate-lm",
        delegate_max_calls_per_turn=3,
        delegate_result_truncation_chars=500,
    )

    assert agent == "daytona-agent"
    options = captured["options"]
    assert options.react_max_iters == 5
    assert options.deep_react_max_iters == 9
    assert options.enable_adaptive_iters is False
    assert options.rlm_max_iterations == 11
    assert options.rlm_max_llm_calls == 17
    assert options.max_depth == 4
    assert options.timeout == 123
    assert options.secret_name == "LITELLM"
    assert options.volume_name is None
    assert options.history_max_turns == 8
    assert options.interpreter_async_execute is False
    assert options.guardrail_mode == "warn"
    assert options.max_output_chars == 1200
    assert options.min_substantive_chars == 40
    assert options.delegate_lm == "delegate-lm"
    assert options.delegate_max_calls_per_turn == 3
    assert options.delegate_result_truncation_chars == 500
    assert captured["planner_lm"] == "planner-lm"
