"""Unit tests for shared cached runtime-module tool helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.agent.delegation_policy import RuntimeModuleExecutionResult
from fleet_rlm.runtime.tools.runtime_module_helpers import (
    run_cached_runtime_module,
    runtime_metadata,
)
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


def test_run_cached_runtime_module_uses_shared_delegation_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    captured: dict[str, object] = {}

    def _fake_invoke(request):
        captured["agent"] = request.agent
        captured["module_name"] = request.module_name
        captured["module_kwargs"] = request.module_kwargs
        return RuntimeModuleExecutionResult(
            prediction={"answer": "ok"},
            error=None,
            fallback_used=True,
        )

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.runtime_module_helpers.invoke_runtime_module",
        _fake_invoke,
    )

    prediction, error, fallback_used = run_cached_runtime_module(
        agent,
        "memory_tree",
        root_path="/data/memory",
        max_depth=4,
    )

    assert prediction == {"answer": "ok"}
    assert error is None
    assert fallback_used is True
    assert captured == {
        "agent": agent,
        "module_name": "memory_tree",
        "module_kwargs": {"root_path": "/data/memory", "max_depth": 4},
    }


def test_runtime_metadata_reads_prediction_fields_from_dict_and_object() -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    agent._current_depth = 2

    dict_metadata = runtime_metadata(
        agent,
        {"depth": "5", "sub_agent_history": "3"},
        fallback_used=False,
    )
    object_metadata = runtime_metadata(
        agent,
        SimpleNamespace(depth=4, sub_agent_history=1),
        fallback_used=True,
    )

    assert dict_metadata == {
        "depth": 5,
        "sub_agent_history": 3,
        "delegate_lm_fallback": False,
        "runtime_degraded": False,
        "runtime_fallback_used": False,
    }
    assert object_metadata == {
        "depth": 4,
        "sub_agent_history": 1,
        "delegate_lm_fallback": True,
        "runtime_degraded": False,
        "runtime_fallback_used": False,
    }


def test_runtime_metadata_defaults_depth_and_clamps_negative_values() -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    agent._current_depth = 2

    metadata = runtime_metadata(
        agent,
        {"sub_agent_history": "-7"},
        fallback_used=True,
    )

    assert metadata == {
        "depth": 3,
        "sub_agent_history": 0,
        "delegate_lm_fallback": True,
        "runtime_degraded": False,
        "runtime_fallback_used": False,
    }


def test_runtime_metadata_preserves_runtime_degradation_fields() -> None:
    interpreter = SimpleNamespace(
        current_runtime_metadata=lambda: {
            "runtime_degraded": True,
            "runtime_fallback_used": True,
            "runtime_failure_category": "sandbox_error",
            "runtime_failure_phase": "execute",
        }
    )
    agent = RLMReActChatAgent(interpreter=interpreter)
    agent._current_depth = 1

    metadata = runtime_metadata(
        agent,
        {"depth": 2, "sub_agent_history": 0},
        fallback_used=False,
    )

    assert metadata == {
        "depth": 2,
        "sub_agent_history": 0,
        "delegate_lm_fallback": False,
        "runtime_degraded": True,
        "runtime_fallback_used": True,
        "runtime_failure_category": "sandbox_error",
        "runtime_failure_phase": "execute",
    }


def test_run_cached_runtime_module_returns_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = RLMReActChatAgent(interpreter=FakeInterpreter())

    def _fake_invoke(_request):
        return RuntimeModuleExecutionResult(
            prediction=None,
            error={"status": "error", "error": "boom"},
            fallback_used=False,
        )

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.runtime_module_helpers.invoke_runtime_module",
        _fake_invoke,
    )

    prediction, error, fallback_used = run_cached_runtime_module(agent, "memory_tree")

    assert prediction is None
    assert error == {"status": "error", "error": "boom"}
    assert fallback_used is False
