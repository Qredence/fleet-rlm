"""Contract: installed dspy.RLM constructor surface used by RLMFactory."""

from __future__ import annotations

import inspect
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend


def test_dspy_rlm_constructor_uses_max_iterations_not_max_iters() -> None:
    """Lock installed DSPy 3.3.Xb naming so upgrades fail here, not at runtime."""
    import dspy

    parameters = inspect.signature(dspy.RLM.__init__).parameters
    assert "max_iterations" in parameters
    assert "max_iters" not in parameters
    for name in (
        "signature",
        "max_llm_calls",
        "max_output_chars",
        "tools",
        "sub_lm",
        "interpreter",
    ):
        assert name in parameters, f"missing constructor field: {name}"


def test_dspy_rlm_accepts_file_tool_names_and_fresh_custom_interpreters() -> None:
    import dspy

    from fleet_rlm.rlm.signature import FleetRLMSignature

    class Interpreter:
        pass

    def read_attachment(attachment_id: str) -> dict[str, str]:
        return {"attachment_id": attachment_id}

    def create_artifact(kind: str, content: str, title: str | None = None) -> dict[str, str | None]:
        return {"kind": kind, "content": content, "title": title}

    first_interpreter = Interpreter()
    second_interpreter = Interpreter()
    explicit_tool = dspy.Tool(lambda key: {"key": key}, name="lookup", desc="Lookup registered knowledge")
    first = dspy.RLM(
        FleetRLMSignature,
        tools=[read_attachment, create_artifact, explicit_tool],
        interpreter=first_interpreter,
    )
    second = dspy.RLM(
        FleetRLMSignature,
        tools=[read_attachment, create_artifact],
        interpreter=second_interpreter,
    )

    assert set(first.tools) == {"read_attachment", "create_artifact", "lookup"}
    assert set(second.tools) == {"read_attachment", "create_artifact"}
    assert first is not second
    assert first._interpreter is first_interpreter
    assert second._interpreter is second_interpreter


def test_pinned_json_adapter_formats_typed_inputs_and_native_rlm_action_outputs() -> None:
    from fleet_rlm.rlm.signature import FleetRLMSignature
    from tests.unit.backend.rlm.test_signature_inputs import _payload

    adapter = dspy.JSONAdapter(use_native_function_calling=True)
    messages = adapter.format(FleetRLMSignature, [], _payload())
    assert messages[0]["role"] == "system"
    assert "session_context" in messages[-1]["content"]
    assert "report-builder" in messages[-1]["content"]
    assert adapter.use_native_function_calling is True

    rlm = dspy.RLM(FleetRLMSignature)
    assert set(rlm.generate_action.signature.output_fields) == {"reasoning", "code"}
    assert "completed" not in rlm.generate_action.signature.output_fields


def test_pinned_json_adapter_keeps_protocol_markers_outside_action_code() -> None:
    rlm = dspy.RLM("request -> answer: str")
    adapter = dspy.JSONAdapter(use_native_function_calling=True)
    completion = (
        '{"reasoning":"submit the verified result","code":"SUBMIT(answer=\\"1\\")"}'
        "\n[[ ## variables_info ## ]]\n[[ ## repl_history ## ]]\n"
        '{"internal":"framework metadata"}'
    )

    parsed = adapter.parse(rlm.generate_action.signature, completion)
    prediction = dspy.Prediction(**parsed)

    assert type(prediction) is dspy.Prediction
    assert prediction.reasoning == "submit the verified result"
    assert prediction.code == 'SUBMIT(answer="1")'
    assert "[[ ##" not in prediction.code


@pytest.mark.asyncio
async def test_native_json_rlm_computes_and_submits_verified_pi_digit_without_retry() -> None:
    from dspy.utils import DummyLM

    compute_digit = inspect.cleandoc(
        """
        from decimal import Decimal, localcontext

        digit_index = 14952
        with localcontext() as decimal_context:
            decimal_context.prec = digit_index + 100
            constant = Decimal(426880) * Decimal(10005).sqrt()
            multiplier = 1
            linear = 13591409
            exponential = 1
            factor = 6
            series = Decimal(linear)
            for iteration_index in range(1, digit_index // 14 + 12):
                multiplier = (factor**3 - 16 * factor) * multiplier // iteration_index**3
                linear += 545140134
                exponential *= -262537412640768000
                series += Decimal(multiplier * linear) / Decimal(exponential)
                factor += 12
            pi_text = format(constant / series, "f")
        digit = pi_text[digit_index + 1]
        window = pi_text[digit_index - 9:digit_index + 12]
        _out = window
        """
    )
    adapter = dspy.JSONAdapter(use_native_function_calling=True)
    lm = DummyLM(
        [
            {
                "reasoning": "Compute the requested digit with the standard library.",
                "code": compute_digit,
            },
            {
                "reasoning": "Verify the computed window against the known reference prefix.",
                "code": (
                    'assert window == "049449650117321313895"\nverified = digit == "1"\n_out = f"{window}:{verified}"'
                ),
            },
            {
                "reasoning": "Submit the verified digit from the persistent interpreter.",
                "code": "SUBMIT(answer=digit)",
            },
        ],
        adapter=adapter,
    )
    rlm = dspy.RLM(
        "request -> answer: str",
        max_iterations=3,
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
    )

    with dspy.context(lm=lm, adapter=adapter):
        prediction = await rlm.acall(request="Tell me the 14952th digit after the decimal point of Pi")

    assert prediction.answer == "1"
    assert prediction.trajectory[0]["output"] == "049449650117321313895"
    assert prediction.trajectory[1]["output"] == "049449650117321313895:True"
    assert len(lm.history) == 3


@pytest.mark.asyncio
async def test_pinned_async_rlm_creates_fresh_native_history_and_honors_output_bound() -> None:
    from dspy.primitives.repl_types import REPLHistory

    class Actions:
        def __init__(self) -> None:
            self.calls = 0
            self.initial_histories: list[REPLHistory] = []

        async def acall(self, **kwargs: Any) -> dspy.Prediction:
            history = kwargs["repl_history"]
            assert type(history) is REPLHistory
            self.calls += 1
            if self.calls in (1, 3):
                assert len(history.entries) == 0
                self.initial_histories.append(history)
                return dspy.Prediction(reasoning="create long output", code="_out = 'x' * 64")
            assert history.max_output_chars == 12
            assert "x" * 64 not in history.format()
            return dspy.Prediction(reasoning="submit typed output", code="SUBMIT(answer='ok')")

    actions = Actions()
    rlm = dspy.RLM(
        "request -> answer: str",
        max_iterations=2,
        max_output_chars=12,
        interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
    )
    rlm.generate_action = actions

    first = await rlm.acall(request="first")
    second = await rlm.acall(request="second")

    assert first.answer == second.answer == "ok"
    assert first.final_reasoning == second.final_reasoning == "submit typed output"
    assert len(actions.initial_histories) == 2
    assert actions.initial_histories[0] is not actions.initial_histories[1]
