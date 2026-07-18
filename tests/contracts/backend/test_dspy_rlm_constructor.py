"""Contract: installed dspy.RLM constructor surface used by RLMFactory."""

from __future__ import annotations

import inspect
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter


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
    assert first._interpreter is first_interpreter  # noqa: SLF001 - installed DSPy contract
    assert second._interpreter is second_interpreter  # noqa: SLF001 - installed DSPy contract


def test_rlm_package_has_no_private_observable_override() -> None:
    from pathlib import Path

    package = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "rlm"
    assert not (package / "observable.py").exists()


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
