from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def test_variable_mode_module_collects_sub_tools_and_uses_tight_output_cap(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMVariableSignature
    from fleet_rlm.runtime.modules import variable_mode

    interpreter = SimpleNamespace(sub_rlm=lambda prompt: prompt, sub_rlm_batched=lambda prompts: prompts)
    captured: dict[str, Any] = {}

    def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock(spec=dspy.Module)

    monkeypatch.setattr(variable_mode, "create_runtime_rlm", fake_create_runtime_rlm)
    module = variable_mode.RLMVariableExecutionModule(interpreter=interpreter, max_iterations=7, max_llm_calls=13)

    assert captured["signature"] is RLMVariableSignature
    assert captured["interpreter"] is interpreter
    assert captured["max_iterations"] == 7
    assert captured["max_llm_calls"] == 13
    assert captured["max_output_chars"] == variable_mode.VARIABLE_MODE_MAX_OUTPUT_CHARS
    assert captured["tools"] == [interpreter.sub_rlm, interpreter.sub_rlm_batched]
    assert module is not None


def test_variable_mode_forward_preserves_signature_kwargs(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
    from fleet_rlm.runtime.modules import variable_mode

    interpreter = SimpleNamespace()
    fake_rlm = MagicMock(return_value=dspy.Prediction(summary="Summary", key_points=["one"], coverage_pct=90))
    monkeypatch.setattr(variable_mode, "create_runtime_rlm", lambda **kwargs: fake_rlm)

    module = variable_mode.RLMVariableExecutionModule(signature=SummarizeLongDocument, interpreter=interpreter)
    result = module(document="doc body", focus="latency")

    fake_rlm.assert_called_once_with(document="doc body", focus="latency")
    assert result.summary == "Summary"
    assert result.key_points == ["one"]
    assert result.coverage_pct == 90


def test_build_variable_mode_rlm_returns_wrapper(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.modules import variable_mode

    monkeypatch.setattr(variable_mode, "create_runtime_rlm", lambda **kwargs: MagicMock(spec=dspy.Module))

    module = variable_mode.build_variable_mode_rlm(interpreter=SimpleNamespace())

    assert isinstance(module, variable_mode.RLMVariableExecutionModule)


def test_runtime_module_registry_flags_and_signature_fields_are_stable() -> None:
    from fleet_rlm.runtime.agent.signatures import (
        GroundedAnswerWithCitations,
        ReflectAndReviseWorkspaceStep,
        RLMVariableSignature,
    )
    from fleet_rlm.runtime.modules.registry import RUNTIME_MODULE_NAMES, RUNTIME_MODULE_REGISTRY

    assert {
        "summarize_long_document",
        "extract_from_logs",
        "grounded_answer",
        "recursive_workspace",
    } <= RUNTIME_MODULE_NAMES
    assert RUNTIME_MODULE_REGISTRY["summarize_long_document"].variable_mode is True
    assert RUNTIME_MODULE_REGISTRY["extract_from_logs"].variable_mode is True
    assert RUNTIME_MODULE_REGISTRY["grounded_answer"].variable_mode is False

    assert set(RLMVariableSignature.input_fields) == {"task", "prompt"}
    assert set(RLMVariableSignature.output_fields) == {"answer"}
    assert {"query", "evidence_chunks", "response_style"} <= set(GroundedAnswerWithCitations.input_fields)
    assert {"answer", "citations", "confidence", "coverage_notes"} <= set(GroundedAnswerWithCitations.output_fields)
    assert {"next_action", "revised_plan", "rationale", "confidence"} <= set(
        ReflectAndReviseWorkspaceStep.output_fields
    )


def test_runtime_module_class_caches_generated_wrappers() -> None:
    from fleet_rlm.runtime.agent.signatures import CodeChangePlan
    from fleet_rlm.runtime.modules.registry import RuntimeModuleDefinition, runtime_module_class

    definition = RuntimeModuleDefinition(
        signature=CodeChangePlan,
        class_name="CodeChangePlanModule",
        doc="Runtime wrapper for code plans.",
    )

    first = runtime_module_class(definition)
    second = runtime_module_class(definition)

    assert first is second
    assert first.__name__ == "CodeChangePlanModule"
    assert first.__doc__ == "Runtime wrapper for code plans."


def test_build_runtime_module_routes_variable_mode_and_caches_instances(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.modules import registry as module_registry
    from fleet_rlm.runtime.modules.factory import RuntimeModuleBuildConfig

    interpreter = SimpleNamespace(sub_rlm=lambda prompt: prompt, sub_rlm_batched=lambda prompts: prompts)
    captured: list[dict[str, Any]] = []

    def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
        captured.append(kwargs)
        return MagicMock(spec=dspy.Module)

    monkeypatch.setattr("fleet_rlm.runtime.modules.variable_mode.create_runtime_rlm", fake_create_runtime_rlm)

    built = module_registry.build_runtime_module(
        "summarize_long_document",
        interpreter=interpreter,
        max_iterations=5,
        max_llm_calls=20,
        verbose=False,
    )
    cache: dict[str, Any] = {}
    config = RuntimeModuleBuildConfig(interpreter=interpreter, max_iterations=5, max_llm_calls=20, verbose=False)
    cached_first = module_registry.get_or_build_runtime_module(cache, "summarize_long_document", config=config)
    cached_second = module_registry.get_or_build_runtime_module(cache, "summarize_long_document", config=config)

    assert isinstance(built, module_registry.RLMVariableExecutionModule)
    assert cached_first is cached_second
    assert len(captured) == 2


def test_build_runtime_module_rejects_unknown_names() -> None:
    from fleet_rlm.runtime.modules.registry import build_runtime_module

    try:
        build_runtime_module(
            "missing-module",
            interpreter=SimpleNamespace(),
            max_iterations=1,
            max_llm_calls=1,
            verbose=False,
        )
    except ValueError as exc:
        assert "Unknown runtime module" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown runtime module")
