from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def test_interpreter_delegation_tools_collects_sub_rlm_callables() -> None:
    from fleet_rlm.runtime.modules.factory import interpreter_delegation_tools

    interpreter = SimpleNamespace(sub_rlm=lambda prompt: prompt, sub_rlm_batched=lambda prompts: prompts)

    tools = interpreter_delegation_tools(interpreter)

    assert tools == [interpreter.sub_rlm, interpreter.sub_rlm_batched]
    assert interpreter_delegation_tools(None) == []
    assert interpreter_delegation_tools(SimpleNamespace()) == []


def test_sandbox_types_serialize_to_json_dicts() -> None:
    import json

    from fleet_rlm.runtime.sandbox_types import ActiveSkills, LargeDocument, WorkspaceContext

    doc = LargeDocument(text="body text", source_url="https://example.com", metadata={"status": "ok"})
    payload = json.loads(doc.to_sandbox().decode("utf-8"))
    assert payload == {"text": "body text", "source_url": "https://example.com", "metadata": {"status": "ok"}}
    assert doc.sandbox_assignment("document", "_raw_document") == "document = json.loads(_raw_document)"
    assert "source_url" in doc.rlm_preview()

    ctx = WorkspaceContext(context_paths=["/tmp/a.pdf"], manifest={"/tmp/a.pdf": "1024"})
    payload = json.loads(ctx.to_sandbox().decode("utf-8"))
    assert payload["context_paths"] == ["/tmp/a.pdf"]
    assert payload["manifest"] == {"/tmp/a.pdf": "1024"}
    assert "manifest.json" in ctx.rlm_preview()

    skills = ActiveSkills(
        selected=["long-context"],
        catalog={"long-context": "Process large context"},
        instructions={"long-context": "SECRET FULL MARKDOWN"},
        sources={"long-context": "scaffold:fleet_rlm.scaffold.skills.long-context.SKILL.md"},
    )
    payload = json.loads(skills.to_sandbox().decode("utf-8"))
    assert payload["selected"] == ["long-context"]
    assert payload["instructions"]["long-context"] == "SECRET FULL MARKDOWN"
    preview = skills.rlm_preview()
    assert "long-context" in preview
    assert "Process large context" in preview
    assert "SECRET FULL MARKDOWN" not in preview


def test_create_runtime_rlm_without_llm_tools_removes_callback_instructions() -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
        include_llm_tools=False,
    )

    instructions = rlm.generate_action.signature.instructions
    assert "`llm_query(prompt)`" not in instructions
    assert "`llm_query_batched(prompts)`" not in instructions
    assert "semantic callbacks are disabled" in instructions
    assert isinstance(rlm, dspy.Module)


def test_streaming_rlm_emits_action_and_result_steps() -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    events: list[dict[str, Any]] = []
    interpreter = SimpleNamespace(_turn_step_callback=events.append)

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )

    action = dspy.Prediction(reasoning="check lengths", code="print(len(user_request))")
    rlm.generate_action._inner = MagicMock(return_value=action)
    prediction = rlm.generate_action(variables_info=[], repl_history=None, iteration="2/2")

    assert prediction is action
    assert [event["phase"] for event in events] == ["rlm_reasoning", "rlm_tool_call"]
    assert events[0]["iteration"] == 1
    assert events[1]["code"] == "print(len(user_request))"

    from dspy.primitives.repl_types import REPLHistory

    history = rlm._process_execution_result(
        action,
        "print(len(user_request))",
        "12",
        REPLHistory(),
        ["response"],
    )
    assert not isinstance(history, dspy.Prediction)
    assert events[-1]["phase"] == "rlm_tool_result"
    assert events[-1]["output"] == "12"


def test_streaming_rlm_records_real_repl_execution_span(monkeypatch) -> None:
    from fleet_rlm.integrations.observability import mlflow_context
    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    captured: list[dict[str, Any]] = []

    class FakeSpan:
        def __init__(self, name: str, span_type: str | None, attributes: dict[str, Any] | None) -> None:
            self.record = {"name": name, "span_type": span_type, "attributes": attributes or {}, "status": "OK"}

        def __enter__(self) -> "FakeSpan":
            captured.append(self.record)
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def set_inputs(self, inputs: Any) -> None:
            self.record["inputs"] = inputs

        def set_outputs(self, outputs: Any) -> None:
            self.record["outputs"] = outputs

        def set_status(self, status: str) -> None:
            self.record["status"] = status

    fake_mlflow = SimpleNamespace(
        get_current_active_span=lambda: object(),
        start_span=lambda name, span_type=None, attributes=None: FakeSpan(name, span_type, attributes),
    )
    monkeypatch.setattr(
        mlflow_context,
        "_runtime_module",
        lambda: SimpleNamespace(
            _import_mlflow=lambda: fake_mlflow,
            logger=SimpleNamespace(debug=lambda *args, **kwargs: None),
        ),
    )

    class FakeRepl:
        def execute(self, code: str, variables: dict[str, Any]) -> str:
            assert code == "print(user_request)"
            assert variables == {"user_request": "hello"}
            return "hello"

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )
    rlm.generate_action.current_iteration = 2

    result = rlm._execute_code(FakeRepl(), "print(user_request)", {"user_request": "hello"})

    assert result == "hello"
    assert captured[0]["name"] == "fleet_rlm.rlm_repl_execute"
    assert captured[0]["span_type"] == "TOOL"
    assert captured[0]["attributes"]["fleet_rlm.tool_name"] == "repl_execute"
    assert captured[0]["attributes"]["fleet_rlm.rlm_iteration"] == "2"
    assert captured[0]["inputs"]["code"] == "print(user_request)"
    assert captured[0]["outputs"]["status"] == "ok"
    assert captured[0]["outputs"]["result"] == "hello"


def test_streaming_rlm_scopes_json_adapter_without_disabling_semantic_callbacks(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm
    from fleet_rlm.runtime.sandbox_types import ActiveSkills

    interpreter = SimpleNamespace(semantic_callbacks_enabled=True)
    observed: dict[str, Any] = {}

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )

    def fake_forward(self: Any, **kwargs: Any) -> dspy.Prediction:
        _ = self, kwargs
        observed["semantic_callbacks_enabled_during_call"] = interpreter.semantic_callbacks_enabled
        observed["adapter_during_call"] = dspy.settings.adapter
        return dspy.Prediction(response="done")

    monkeypatch.setattr(
        "fleet_rlm.runtime.modules.factory._DSPY_RLM_BASE.forward",
        fake_forward,
    )

    result = rlm.forward(
        user_request="inspect", core_memory="", history=dspy.History(messages=[]), active_skills=ActiveSkills()
    )

    assert result.response == "done"
    assert observed["semantic_callbacks_enabled_during_call"] is True
    assert isinstance(observed["adapter_during_call"], dspy.JSONAdapter)
    assert interpreter.semantic_callbacks_enabled is True


def test_streaming_rlm_applies_action_max_tokens_to_action_predictor() -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    observed: dict[str, Any] = {}
    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        max_output_chars=5000,
        action_max_tokens=1234,
        verbose=False,
    )

    class FakeInner:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            observed.update(kwargs)
            return dspy.Prediction(reasoning="bounded", code="SUBMIT(response='ok')")

    rlm.generate_action._inner = FakeInner()
    rlm.generate_action(variables_info=[], repl_history=None, iteration="1/2")

    assert observed["config"]["max_tokens"] == 1234


def test_streaming_rlm_compacts_repl_history_for_action_prompt() -> None:
    import dspy
    from dspy.primitives.repl_types import REPLHistory

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    history = REPLHistory()
    for index in range(10):
        history = history.append(
            reasoning=f"reasoning {index}" * 200,
            code=f"print('step {index}')\n" + ("x = 1\n" * 400),
            output=f"output {index}\n" + ("large output\n" * 400),
        )

    observed: dict[str, Any] = {}
    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )

    class FakeInner:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            observed.update(kwargs)
            return dspy.Prediction(reasoning="bounded", code="SUBMIT(response='ok')")

    rlm.generate_action._inner = FakeInner()
    rlm.generate_action(variables_info=[], repl_history=history, iteration="1/2")

    compact_history = observed["repl_history"]
    assert len(history.entries) == 10
    assert len(compact_history.entries) == 5
    assert "Compressed 6 earlier REPL steps" in compact_history.entries[0].reasoning
    assert "step 9" in compact_history.entries[-1].code
    assert len(compact_history.format()) < len(history.format())


def test_no_callback_rlm_scopes_disabled_semantic_callbacks(monkeypatch) -> None:
    import dspy

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import _NoCallbackRLM, create_runtime_rlm
    from fleet_rlm.runtime.sandbox_types import ActiveSkills

    interpreter = SimpleNamespace(semantic_callbacks_enabled=True)
    observed: dict[str, Any] = {}

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=interpreter,
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
        include_llm_tools=False,
    )
    assert isinstance(rlm, _NoCallbackRLM)

    def fake_forward(self: Any, **kwargs: Any) -> dspy.Prediction:
        observed["semantic_callbacks_enabled_during_call"] = interpreter.semantic_callbacks_enabled
        observed["adapter_during_call"] = dspy.settings.adapter
        return dspy.Prediction(response="done")

    monkeypatch.setattr(
        "fleet_rlm.runtime.modules.factory._StreamingRLM.forward",
        fake_forward,
    )

    result = rlm.forward(
        user_request="inspect", core_memory="", history=dspy.History(messages=[]), active_skills=ActiveSkills()
    )

    assert result.response == "done"
    assert observed["semantic_callbacks_enabled_during_call"] is False
    assert isinstance(observed["adapter_during_call"], dspy.JSONAdapter)
    assert interpreter.semantic_callbacks_enabled is True


def test_runtime_module_registry_flags_and_signature_fields_are_stable() -> None:
    from fleet_rlm.runtime.agent.signatures import (
        GroundedAnswerWithCitations,
        ReflectAndReviseWorkspaceStep,
        RLMDocumentTurnSignature,
        RLMTurnSignature,
        RLMWorkspaceTurnSignature,
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

    assert set(RLMTurnSignature.input_fields) == {"user_request", "core_memory", "history", "active_skills"}
    assert set(RLMTurnSignature.output_fields) == {"response"}
    assert set(RLMDocumentTurnSignature.input_fields) == {
        "user_request",
        "core_memory",
        "history",
        "document",
        "active_skills",
    }
    assert set(RLMDocumentTurnSignature.output_fields) == {"response"}
    assert set(RLMWorkspaceTurnSignature.input_fields) == {
        "user_request",
        "core_memory",
        "history",
        "context",
        "active_skills",
    }
    assert set(RLMWorkspaceTurnSignature.output_fields) == {"response"}
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

    from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
    from fleet_rlm.runtime.modules import registry as module_registry
    from fleet_rlm.runtime.modules.factory import VARIABLE_MODE_MAX_OUTPUT_CHARS, RuntimeModuleBuildConfig

    interpreter = SimpleNamespace(sub_rlm=lambda prompt: prompt, sub_rlm_batched=lambda prompts: prompts)
    captured: list[dict[str, Any]] = []

    def fake_create_runtime_rlm(**kwargs: Any) -> MagicMock:
        captured.append(kwargs)
        return MagicMock(spec=dspy.Module)

    monkeypatch.setattr(module_registry, "create_runtime_rlm", fake_create_runtime_rlm)

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

    assert built is not None
    assert cached_first is cached_second
    assert len(captured) == 2
    assert captured[0]["signature"] is SummarizeLongDocument
    assert captured[0]["max_output_chars"] == VARIABLE_MODE_MAX_OUTPUT_CHARS
    assert captured[0]["tools"] == [interpreter.sub_rlm, interpreter.sub_rlm_batched]


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


def test_execute_iteration_returns_repl_history_on_single_timeout() -> None:
    """On a single action-gen timeout, _execute_iteration must return the
    (updated) REPLHistory — not None — so the RLM loop contract
    (``history = result``) holds and dspy's _extract_fallback can salvage."""
    import dspy
    from dspy.primitives.repl_types import REPLHistory

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )
    rlm.generate_action = MagicMock(side_effect=dspy.LMError("timeout"))
    history = REPLHistory(max_output_chars=1500)

    # Base dspy.RLM.forward calls _execute_iteration positionally:
    # (repl, variables, history, iteration, input_args, output_field_names)
    result = rlm._execute_iteration(SimpleNamespace(), [], history, 0, {}, ["response"])

    assert result is not None
    assert isinstance(result, REPLHistory)
    assert len(result.entries) == 1
    assert "[Timeout]" in (result.entries[0].output or "")
    assert rlm._consecutive_timeouts == 1


def test_execute_iteration_raises_after_repeated_timeouts() -> None:
    """After 2 consecutive action-gen timeouts, _execute_iteration raises so
    EscalatingFleetModule._run_rlm can warn + retry with reduced context."""
    import dspy
    import pytest
    from dspy.primitives.repl_types import REPLHistory

    from fleet_rlm.runtime.agent.signatures import RLMTurnSignature
    from fleet_rlm.runtime.modules.factory import create_runtime_rlm

    rlm = create_runtime_rlm(
        signature=RLMTurnSignature,
        interpreter=SimpleNamespace(),
        max_iterations=2,
        max_llm_calls=3,
        verbose=False,
    )
    rlm.generate_action = MagicMock(side_effect=dspy.LMError("timeout"))
    rlm._consecutive_timeouts = 1  # one prior timeout already recorded
    history = REPLHistory(max_output_chars=1500)

    with pytest.raises(dspy.LMError):
        rlm._execute_iteration(SimpleNamespace(), [], history, 0, {}, ["response"])
