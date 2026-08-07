"""Observation contracts for the product-owned Daytona interpreter boundary."""

from __future__ import annotations

import pytest

from fleet_rlm.daytona.broker_source import FINAL_OUTPUT_MARKER
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend, sandbox_backend
from fleet_rlm.rlm.errors import TurnNoProgressError
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


def test_interpreter_streams_stdout_deltas_and_finishes_with_one_canonical_output() -> None:
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute('print("first", flush=True)\nprint("second", flush=True)')

    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    deltas = [item for item in outputs if item.is_delta]
    finals = [item for item in outputs if not item.is_delta]
    assert result == "first\nsecond\n"
    assert deltas
    assert "first" in "".join(item.output for item in deltas)
    assert "second" in "".join(item.output for item in deltas)
    assert finals == [outputs[-1]]
    assert finals[0].is_final is True
    assert finals[0].stream_id == outputs[0].stream_id
    assert finals[0].output == result


def test_interpreter_hides_private_submit_marker_from_stdout_deltas() -> None:
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        output_fields=[{"name": "answer", "type": "str"}],
    )
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute('print("before")\nSUBMIT(answer="private __FLEET_FINAL_OUTPUT__ suffix")')

    assert result.output == {"answer": "private __FLEET_FINAL_OUTPUT__ suffix"}
    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    assert all("__FLEET_FINAL_OUTPUT__" not in item.output for item in outputs)
    assert all('"private"' not in item.output for item in outputs)
    assert outputs[-1].output == "FINAL submitted"


def test_interpreter_preserves_literal_marker_before_hiding_submit_frame() -> None:
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        output_fields=[{"name": "answer", "type": "str"}],
    )
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute(f'print("before {FINAL_OUTPUT_MARKER} ordinary")\nSUBMIT(answer="private")')

    assert result.output == {"answer": "private"}
    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    deltas = [item.output for item in outputs if item.is_delta]
    assert "".join(deltas) == f"before {FINAL_OUTPUT_MARKER} ordinary\n"
    assert outputs[-1].output == "FINAL submitted"


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


def test_empty_code_returns_direct_feedback_then_repetition_stops_the_turn() -> None:
    class Backend:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, code: str, variables=None):
            del code, variables
            self.calls += 1
            return ""

        def close(self) -> None:
            return None

    backend = Backend()
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.bind_observer(observed.append, max_chars=1_000)

    first = interpreter.execute(" \n\t")

    assert first == "[Error] No executable code was provided; execute useful Python or call SUBMIT."
    assert backend.calls == 0
    with pytest.raises(TurnNoProgressError, match="repeated tool calls made no progress"):
        interpreter.execute("")

    assert backend.calls == 0
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
    assert observed[2].output == "Execution error"
    assert observed[6].output == "Execution failed"


def test_f_string_backslash_syntax_error_gets_focused_native_repair_feedback() -> None:
    from fleet_rlm.daytona.interpreter import BackendExecutionResult

    class SyntaxBackend:
        def run(self, code: str, variables=None):
            del code, variables
            return BackendExecutionResult(error="SyntaxError: f-string expression part cannot include a backslash")

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=SyntaxBackend())

    result = interpreter.execute("generated_f_string_code")

    assert result.startswith("[Error]")
    assert "f-string expression part cannot include a backslash" in result
    assert "Build the escaped fragment before the f-string expression" in result


def test_sandbox_backend_requires_co_located_broker_and_ignores_code_interpreter() -> None:
    class CodeInterpreter:
        def __init__(self) -> None:
            self.created: list[object] = []
            self.deleted: list[object] = []

        def create_context(self) -> object:
            context = object()
            self.created.append(context)
            return context

        def run_code(self, code: str, *, context: object, timeout: int | None = None):
            del code, timeout
            assert context is self.created[-1]
            return type("Result", (), {"stdout": "ok", "stderr": "", "error": None})()

        def delete_context(self, context: object) -> None:
            self.deleted.append(context)

    code_interpreter = CodeInterpreter()
    sandbox = type("Sandbox", (), {"code_interpreter": code_interpreter})()
    backend = sandbox_backend(sandbox)

    with pytest.raises(DaytonaAdapterError, match="co-located broker"):
        backend.run("print('must use broker')")
    backend.close()

    assert code_interpreter.created == []
    assert code_interpreter.deleted == []
