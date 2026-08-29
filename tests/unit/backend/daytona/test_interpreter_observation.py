"""Observation contracts for the product-owned Daytona interpreter boundary."""

from __future__ import annotations

import pytest
from dspy.primitives.code_interpreter import CodeExecutionError

from fleet_rlm.daytona.broker import FINAL_OUTPUT_MARKER
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    sandbox_backend,
)
from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted, ToolCompleted, ToolStarted
from fleet_rlm.rlm.result import RunNoProgressError


def test_non_strict_shutdown_retains_a_broker_with_pending_cleanup() -> None:
    class Broker:
        def __init__(self) -> None:
            self.results = iter((False, True))

        def stop(self, *, strict: bool = False) -> bool:
            del strict
            return next(self.results)

    class Backend:
        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=Backend())
    broker = Broker()
    interpreter._http_broker = broker  # type: ignore[assignment]

    interpreter.shutdown()
    assert interpreter._http_broker is broker
    assert interpreter._shutdown is False

    interpreter.shutdown()
    assert interpreter._http_broker is None
    assert interpreter._shutdown is True


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


def test_interpreter_streams_stdout_deltas_without_a_duplicate_final_frame() -> None:
    """RC-4b: a fully streamed step emits no full-content final re-emit."""
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute('print("first", flush=True)\nprint("second", flush=True)')

    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    assert result == "first\nsecond\n"
    assert outputs
    # Every streamed chunk arrived as a delta; the deltas concatenate to the
    # complete public output, so the step-final flush stays silent.
    assert all(item.is_delta for item in outputs)
    assert "".join(item.output for item in outputs) == result
    assert {item.stream_id for item in outputs} == {outputs[0].stream_id}
    assert all(item.step == 1 for item in outputs)


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


def test_interpreter_final_flush_emits_only_the_unsent_tail() -> None:
    """RC-4b: a partially streamed step flushes just the missing tail."""

    class PartialStreamingBackend:
        def run(self, code, variables=None, *, on_stdout=None):
            del code, variables
            assert on_stdout is not None
            on_stdout("partial ")
            return BackendExecutionResult(stdout="partial tail")

        def close(self) -> None:
            return None

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(backend=PartialStreamingBackend())
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute("print('partial tail')")

    assert result == "partial tail"
    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    assert [item.output for item in outputs] == ["partial ", "tail"]
    assert (outputs[0].is_delta, outputs[0].is_final) == (True, False)
    # The tail closes the stream as a delta: a non-delta tail frame would
    # replace the accumulated content with just the tail in the TUI.
    assert (outputs[1].is_delta, outputs[1].is_final) == (True, True)
    assert outputs[1].stream_id == outputs[0].stream_id
    assert outputs[1].step == outputs[0].step


def test_interpreter_suppresses_output_after_the_submit_final_frame() -> None:
    """RC-4b: no RLMOutput for a step once its SUBMIT final frame is out."""

    class StragglerBackend:
        def __init__(self) -> None:
            self.late_stdout = None

        def run(self, code, variables=None, *, on_stdout=None):
            del code, variables
            assert on_stdout is not None
            on_stdout("before submit\n")
            self.late_stdout = on_stdout
            return BackendExecutionResult(stdout="before submit\n", final={"answer": "ok"})

        def close(self) -> None:
            return None

    backend = StragglerBackend()
    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        output_fields=[{"name": "answer", "type": "str"}],
    )
    interpreter.bind_observer(observed.append, max_chars=1_000)

    result = interpreter.execute('print("before submit")\nSUBMIT(answer="ok")')

    assert result.output == {"answer": "ok"}
    outputs = [item for item in observed if isinstance(item, RLMOutput)]
    deltas = [item for item in outputs if item.is_delta]
    assert "".join(item.output for item in deltas) == "before submit\n"
    assert outputs[-1].output == "FINAL submitted"
    assert (outputs[-1].is_delta, outputs[-1].is_final) == (False, True)

    # A straggler stdout callback arriving AFTER the SUBMIT final frame (the
    # broker poll race observed live) must not emit any more output frames
    # for that step.
    assert backend.late_stdout is not None
    backend.late_stdout("late straggler output\n")

    outputs_after = [item for item in observed if isinstance(item, RLMOutput)]
    assert outputs_after == outputs


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
    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("missing_name + 1")
    error = str(caught.value)

    started = [item for item in observed if isinstance(item, ToolStarted)]
    completed = [item for item in observed if isinstance(item, ToolCompleted)]
    assert [item.input for item in started] == [
        {"prompt_count": 1, "prompt_chars": 14},
        {"prompt_count": 2, "prompt_chars": 6},
    ]
    assert [item.output for item in completed] == [{}, {}]
    assert "private prompt" not in str(started)
    assert "private result" not in str(completed)
    assert error.startswith("name 'missing_name'")
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


def test_empty_code_returns_one_repair_feedback_then_stops_the_turn() -> None:
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

    with pytest.raises(CodeExecutionError) as first_error:
        interpreter.execute(" \n\t")

    assert str(first_error.value) == "No executable code was provided; execute useful Python or call SUBMIT."
    assert backend.calls == 0
    with pytest.raises(CodeExecutionError) as second_error:
        interpreter.execute("")

    assert str(second_error.value) == (
        "Repeated interpreter action produced no progress. "
        "Choose a different action, use the existing output, or call SUBMIT."
    )
    assert backend.calls == 0
    with pytest.raises(RunNoProgressError, match="repeated tool calls made no progress"):
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
        StepStarted,
        RLMCode,
        RLMOutput,
        StepFinished,
    ]
    assert observed[2].output == "Execution error"
    assert observed[6].output == "Execution error"
    assert observed[10].output == "Execution failed"


def test_repeated_interpreter_action_allows_one_bounded_repair() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_observer(lambda _detail: None)

    assert interpreter.execute("_out = 'same'") == "same"
    with pytest.raises(CodeExecutionError) as repair_error:
        interpreter.execute("_out = 'same'")

    assert str(repair_error.value) == (
        "Repeated interpreter action produced no progress. "
        "Choose a different action, use the existing output, or call SUBMIT."
    )
    with pytest.raises(RunNoProgressError, match="repeated tool calls made no progress"):
        interpreter.execute("_out = 'same'")


def test_f_string_backslash_syntax_error_gets_focused_native_repair_feedback() -> None:
    from fleet_rlm.daytona.interpreter import BackendExecutionResult

    class SyntaxBackend:
        def run(self, code: str, variables=None):
            del code, variables
            return BackendExecutionResult(error="SyntaxError: f-string expression part cannot include a backslash")

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=SyntaxBackend())

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("generated_f_string_code")
    result = str(caught.value)

    assert result.startswith("SyntaxError:")
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
