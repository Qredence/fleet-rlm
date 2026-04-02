"""Tests for true-RLM variable-mode execution (Algorithm 1, arXiv 2512.24601v2).

Validates that:
1. RLMVariableSignature has the correct shape (task, prompt → answer)
2. RLMVariableExecutionModule delegates to dspy.RLM with tools
3. build_variable_mode_rlm() factory works
4. Variable-mode routing in rlm_query activates above threshold
5. Registry variable_mode flag propagates to build_runtime_module
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import dspy


# ── Signature shape ──────────────────────────────────────────────────


def test_variable_signature_input_fields():
    from fleet_rlm.runtime.agent.signatures import RLMVariableSignature

    inputs = list(RLMVariableSignature.input_fields.keys())
    assert "task" in inputs
    assert "prompt" in inputs
    assert len(inputs) == 2, f"Expected 2 inputs, got {inputs}"


def test_variable_signature_output_fields():
    from fleet_rlm.runtime.agent.signatures import RLMVariableSignature

    outputs = list(RLMVariableSignature.output_fields.keys())
    assert outputs == ["answer"]


# ── Module construction ──────────────────────────────────────────────


def test_variable_execution_module_creates_rlm():
    """RLMVariableExecutionModule should wrap a dspy.RLM internally."""
    from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

    mock_interp = MagicMock()
    mock_interp.sub_rlm = MagicMock()
    mock_interp.sub_rlm_batched = MagicMock()

    with patch("fleet_rlm.runtime.models.builders.create_runtime_rlm") as mock_create:
        mock_create.return_value = MagicMock(spec=dspy.Module)
        RLMVariableExecutionModule(
            interpreter=mock_interp,
            max_iterations=10,
            max_llm_calls=20,
            verbose=True,
        )
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["max_iterations"] == 10
        assert call_kwargs["max_llm_calls"] == 20
        assert call_kwargs["verbose"] is True
        # sub_rlm and sub_rlm_batched should be passed as tools
        tools = call_kwargs.get("tools")
        assert tools is not None
        assert len(tools) == 2


def test_variable_execution_module_no_sub_rlm_on_interpreter():
    """When interpreter lacks sub_rlm, tools list should be None."""
    from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

    mock_interp = MagicMock(spec=[])  # No sub_rlm attributes

    with patch("fleet_rlm.runtime.models.builders.create_runtime_rlm") as mock_create:
        mock_create.return_value = MagicMock(spec=dspy.Module)
        RLMVariableExecutionModule(interpreter=mock_interp, verbose=False)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("tools") is None


def test_variable_execution_module_forward_delegates():
    """forward(task, prompt) should call the inner RLM with both fields."""
    from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

    mock_interp = MagicMock(spec=[])
    mock_rlm = MagicMock()
    mock_rlm.return_value = dspy.Prediction(answer="42")

    with patch(
        "fleet_rlm.runtime.models.builders.create_runtime_rlm",
        return_value=mock_rlm,
    ):
        module = RLMVariableExecutionModule(interpreter=mock_interp)
        result = module(task="count words", prompt="hello world " * 10000)

    mock_rlm.assert_called_once_with(task="count words", prompt="hello world " * 10000)
    assert result.answer == "42"


# ── Factory ──────────────────────────────────────────────────────────


def test_build_variable_mode_rlm_returns_module():
    from fleet_rlm.runtime.models.builders import (
        RLMVariableExecutionModule,
        build_variable_mode_rlm,
    )

    mock_interp = MagicMock(spec=[])
    with patch("fleet_rlm.runtime.models.builders.create_runtime_rlm"):
        module = build_variable_mode_rlm(interpreter=mock_interp)
    assert isinstance(module, RLMVariableExecutionModule)


# ── Registry variable_mode flag ──────────────────────────────────────


def test_registry_long_document_modules_have_variable_mode():
    from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

    for name in (
        "analyze_long_document",
        "summarize_long_document",
        "extract_from_logs",
    ):
        defn = RUNTIME_MODULE_REGISTRY[name]
        assert defn.variable_mode is True, f"{name} should have variable_mode=True"


def test_registry_short_context_modules_no_variable_mode():
    from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

    for name in ("grounded_answer", "plan_code_change", "memory_tree"):
        defn = RUNTIME_MODULE_REGISTRY[name]
        assert defn.variable_mode is False, f"{name} should have variable_mode=False"


def test_build_runtime_module_variable_mode_returns_variable_module():
    """build_runtime_module with variable_mode=True entry → RLMVariableExecutionModule."""
    from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule
    from fleet_rlm.runtime.models.registry import build_runtime_module

    mock_interp = MagicMock(spec=[])
    with patch(
        "fleet_rlm.runtime.models.registry.RLMVariableExecutionModule"
    ) as mock_cls:
        mock_cls.return_value = MagicMock(spec=RLMVariableExecutionModule)
        build_runtime_module(
            "analyze_long_document",
            interpreter=mock_interp,
            max_iterations=10,
            max_llm_calls=30,
            verbose=False,
        )
        mock_cls.assert_called_once_with(
            interpreter=mock_interp,
            max_iterations=10,
            max_llm_calls=30,
            verbose=False,
        )


def test_build_runtime_module_non_variable_mode_returns_generic():
    """build_runtime_module with variable_mode=False → generic _RuntimeSignatureModule."""
    from fleet_rlm.runtime.models.registry import build_runtime_module

    mock_interp = MagicMock()
    # grounded_answer has a custom module_class, NOT variable_mode
    with patch("fleet_rlm.runtime.models.builders._create_configured_runtime_rlm"):
        module = build_runtime_module(
            "grounded_answer",
            interpreter=mock_interp,
            max_iterations=10,
            max_llm_calls=30,
            verbose=False,
        )
    # Should NOT be RLMVariableExecutionModule
    from fleet_rlm.runtime.models.builders import RLMVariableExecutionModule

    assert not isinstance(module, RLMVariableExecutionModule)


# ── Threshold constant ───────────────────────────────────────────────


def test_variable_mode_threshold_is_reasonable():
    from fleet_rlm.runtime.models.builders import VARIABLE_MODE_THRESHOLD

    assert VARIABLE_MODE_THRESHOLD == 32_000


# ── rlm_query auto-routing ──────────────────────────────────────────


def test_has_interpreter_checks_started():
    from fleet_rlm.runtime.tools.sandbox_delegate_tools import _has_interpreter

    agent = MagicMock()
    agent.interpreter = MagicMock()
    agent.interpreter._started = True
    assert _has_interpreter(agent) is True

    agent.interpreter._started = False
    assert _has_interpreter(agent) is False

    agent.interpreter = None
    assert _has_interpreter(agent) is False
