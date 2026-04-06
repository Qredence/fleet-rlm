from __future__ import annotations

from typing import Any

from fleet_rlm.cli import runtime_factory
import pytest
from fleet_rlm.integrations.config.env import AppConfig
from fleet_rlm.integrations.mcp.server import MCPRuntimeConfig


def test_build_chat_agent_for_runtime_mode_rejects_non_daytona_modes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "fleet_rlm.runtime.factory.build_chat_agent",
        lambda **kwargs: kwargs,
    )

    with pytest.raises(
        ValueError,
        match="Only the Daytona runtime is supported",
    ):
        runtime_factory.build_chat_agent_for_runtime_mode(
            runtime_mode="unexpected-mode"
        )


def test_build_chat_agent_for_runtime_mode_routes_daytona_to_canonical_builder(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_builder(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "daytona-agent"

    monkeypatch.setattr(
        "fleet_rlm.runtime.factory.build_chat_agent",
        _fake_builder,
    )

    agent = runtime_factory.build_chat_agent_for_runtime_mode(
        runtime_mode="daytona_pilot",
        react_max_iters=6,
        timeout=45,
        secret_name="daytona-secret",
        volume_name="daytona-volume",
        planner_lm="planner-lm",
    )

    assert agent == "daytona-agent"
    assert captured["react_max_iters"] == 6
    assert captured["timeout"] == 45
    assert captured["secret_name"] == "daytona-secret"
    assert captured["volume_name"] == "daytona-volume"
    assert captured["planner_lm"] == "planner-lm"


def test_mcp_runtime_config_from_app_config_maps_shared_settings() -> None:
    config = AppConfig(
        interpreter={
            "secrets": ["ALT_SECRET"],
            "volume_name": "mcp-volume",
            "timeout": 222,
            "async_execute": False,
        },
        agent={
            "delegate_model": "openai/gpt-4.1-nano",
            "delegate_max_tokens": 4096,
            "rlm_max_iterations": 14,
            "guardrail_mode": "strict",
            "min_substantive_chars": 61,
        },
        rlm_settings={
            "max_iters": 11,
            "deep_max_iters": 19,
            "enable_adaptive_iters": False,
            "max_llm_calls": 77,
            "max_depth": 5,
            "delegate_max_calls_per_turn": 4,
            "delegate_result_truncation_chars": 654,
            "max_output_chars": 9876,
        },
    )

    cfg = MCPRuntimeConfig.from_app_config(config)

    assert cfg.secret_name == "ALT_SECRET"
    assert cfg.volume_name == "mcp-volume"
    assert cfg.timeout == 222
    assert cfg.react_max_iters == 11
    assert cfg.deep_react_max_iters == 19
    assert cfg.enable_adaptive_iters is False
    assert cfg.rlm_max_iterations == 14
    assert cfg.rlm_max_llm_calls == 77
    assert cfg.rlm_max_depth == 5
    assert cfg.delegate_max_calls_per_turn == 4
    assert cfg.delegate_result_truncation_chars == 654
    assert cfg.interpreter_async_execute is False
    assert cfg.agent_guardrail_mode == "strict"
    assert cfg.agent_min_substantive_chars == 61
    assert cfg.agent_max_output_chars == 9876
    assert cfg.agent_delegate_model == "openai/gpt-4.1-nano"
    assert cfg.agent_delegate_max_tokens == 4096
