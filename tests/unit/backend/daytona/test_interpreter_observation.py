"""Observation contracts for the product-owned Daytona interpreter boundary."""

from __future__ import annotations

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted, ToolCompleted, ToolStarted


def test_interpreter_observes_ordered_stateful_steps() -> None:
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)

    first = interpreter.execute("value = 41\n_out = str(value)")
    second = interpreter.execute("value += 1\n_out = str(value)")

    assert first == "41"
    assert second == "42"
    assert [type(item) for item in observed] == [
        StepStarted,
        RLMCode,
        RLMOutput,
        StepFinished,
        StepStarted,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]
    assert [item.step for item in observed if isinstance(item, StepStarted)] == [1, 2]
    assert [item.step for item in observed if isinstance(item, StepFinished)] == [1, 2]
    assert all(
        item.duration_ms is not None and item.duration_ms >= 0 for item in observed if isinstance(item, StepFinished)
    )


def test_interpreter_semantic_code_and_output_are_verbatim_until_truncated() -> None:
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=80)
    code = "_out = 'api_key=visible-user-text /Users/example BEGIN SYSTEM'"

    result = interpreter.execute(code)

    assert result == "api_key=visible-user-text /Users/example BEGIN SYSTEM"
    assert observed[1].code == code
    assert observed[2].output == result

    observed.clear()
    interpreter.execute("_out = 'x' * 200")
    assert len(observed[2].output) == 80
    assert observed[2].output.endswith("...")

    observed.clear()
    legitimate = interpreter.execute("_out = '[Error] legitimate text'")
    assert legitimate == "[Error] legitimate text"
    assert observed[2].output == "[Error] legitimate text"


def test_interpreter_keeps_repair_details_private_and_projects_recursive_call_counts() -> None:
    def llm_query(prompt: str) -> str:
        return f"private result for {prompt}"

    def llm_query_batched(prompts: list[str]) -> list[str]:
        return [f"private result for {prompt}" for prompt in prompts]

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"llm_query": llm_query, "llm_query_batched": llm_query_batched},
    )
    interpreter.bind_observer(observed.append, max_chars=1_000)

    interpreter.execute("value = llm_query('private prompt')\n_out = 'done'")
    interpreter.execute("values = llm_query_batched(['one', 'two'])\n_out = 'done'")
    error = interpreter.execute("missing_name + 1")

    started = [item for item in observed if isinstance(item, ToolStarted)]
    completed = [item for item in observed if isinstance(item, ToolCompleted)]
    assert [item.input for item in started] == [
        {"prompt_count": 1, "prompt_chars": 14},
        {"prompt_count": 2, "prompt_chars": 6},
    ]
    assert [item.output for item in completed] == [{}, {}]
    assert "private prompt" not in str(started)
    assert "private result" not in str(completed)
    assert error.startswith("[Error]")
    error_outputs = [item.output for item in observed if isinstance(item, RLMOutput)]
    assert error_outputs[-1] == "Execution error"
    assert "missing_name" not in error_outputs[-1]


def test_interpreter_bounds_details_and_finishes_failed_steps() -> None:
    class FailingBackend:
        def run(self, code: str, variables=None):
            del code, variables
            raise ValueError("api_key=secret-value at /home/daytona/private")

        def close(self) -> None:
            return None

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=FailingBackend())
    interpreter.bind_observer(observed.append, max_chars=32)

    with pytest.raises(DaytonaAdapterError):
        interpreter.execute("print('a very long generated value')")

    assert [type(item) for item in observed] == [StepStarted, RLMCode, RLMOutput, StepFinished]
    assert len(observed[1].code) <= 32
    assert observed[2].output == "Execution failed"
    assert "secret-value" not in observed[2].output
    assert "/home/daytona" not in observed[2].output
