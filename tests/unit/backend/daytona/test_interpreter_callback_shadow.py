"""DSPy 3.3.1 interpreter-callback shadow contracts."""

from __future__ import annotations

from typing import Any

import pytest
from dspy.primitives.code_interpreter import CodeInterpreterError
from dspy.utils.callback import BaseCallback

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.observability.callback_shadow import (
    CallbackShadowRecorder,
    compare_callback_records,
)


def test_shadow_recorder_matches_manual_interpreter_lifecycle() -> None:
    recorder = CallbackShadowRecorder()
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        callbacks=[recorder],
    )

    interpreter.start()
    assert interpreter.execute("_out = 'ok'") == "ok"
    interpreter.shutdown()

    records = recorder.records()
    assert [record.operation for record in records] == [
        "startup",
        "execute",
        "shutdown",
    ]
    assert all(record.status == "completed" for record in records)
    assert all(record.duration_ms >= 0 for record in records)
    assert all(record.parent_call_id is None for record in records)
    assert compare_callback_records(records, records).semantic_differences == ()


def test_shadow_recorder_tracks_recoverable_and_terminal_exceptions() -> None:
    class BrokenBackend:
        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            raise CodeInterpreterError("terminal")

        def close(self) -> None:
            return None

    recorder = CallbackShadowRecorder()
    interpreter = DaytonaCodeInterpreter(
        backend=BrokenBackend(),
        callbacks=[recorder],
    )

    with pytest.raises(CodeInterpreterError):
        interpreter.execute("_out = 'never'")
    interpreter.shutdown()

    records = recorder.records()
    assert [record.operation for record in records] == ["execute", "startup", "shutdown"]
    execute = records[0]
    assert execute.status == "failed"
    assert execute.exception_category == "CodeInterpreterError"
    assert execute.parent_call_id is None


def test_shadow_tool_callback_is_nested_under_execute_and_has_one_terminal() -> None:
    recorder = CallbackShadowRecorder()

    def helper(value: str) -> str:
        return f"done:{value}"

    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"helper": helper},
        callbacks=[recorder],
    )

    assert interpreter.execute("result = helper(value='a')\n_out = result") == "done:a"
    interpreter.shutdown()

    records = recorder.records()
    tool_records = [record for record in records if record.operation == "tool_call"]
    assert len(tool_records) == 1
    tool = tool_records[0]
    execute = next(record for record in records if record.operation == "execute")
    assert tool.tool_name == "helper"
    assert tool.status == "completed"
    assert tool.parent_call_id == execute.call_id
    assert [record.operation for record in records].count("tool_call") == 1


def test_callback_handler_failures_are_fail_soft_for_result_and_product_observer() -> None:
    class ExplodingCallback(BaseCallback):
        def on_interpreter_startup_start(self, **_kwargs: Any) -> None:
            raise RuntimeError("callback start failure")

        def on_interpreter_execute_end(self, **_kwargs: Any) -> None:
            raise RuntimeError("callback end failure")

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        callbacks=[ExplodingCallback()],
    )
    interpreter.bind_observer(observed.append)

    assert interpreter.execute("_out = 'unchanged'") == "unchanged"
    interpreter.shutdown()
    assert [type(item).__name__ for item in observed] == [
        "StepStarted",
        "RLMCode",
        "RLMOutput",
        "StepFinished",
    ]


def test_callback_export_failure_is_fail_soft() -> None:
    def explode(_record: object) -> None:
        raise RuntimeError("MLflow export unavailable")

    recorder = CallbackShadowRecorder(exporter=explode)
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        callbacks=[recorder],
    )

    assert interpreter.execute("_out = 'export-failure-does-not-change-result'") == (
        "export-failure-does-not-change-result"
    )
    interpreter.shutdown()
    assert len(recorder.records()) == 3


def test_tool_failure_callback_is_terminal_and_sanitized() -> None:
    def helper(value: int) -> str:
        if value == 2:
            raise RuntimeError("provider secret and private path")
        return str(value)

    recorder = CallbackShadowRecorder()
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"helper": helper},
        callbacks=[recorder],
    )

    with pytest.raises(RuntimeError):
        interpreter.execute("helper(value=2)")
    interpreter.shutdown()

    tool = next(record for record in recorder.records() if record.operation == "tool_call")
    assert tool.status == "failed"
    assert tool.exception_category == "RuntimeError"
    assert "provider secret" not in str(tool)
    assert "private path" not in str(tool)


def test_callback_parity_classifies_duration_only_differences() -> None:
    recorder = CallbackShadowRecorder()
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        callbacks=[recorder],
    )
    assert interpreter.execute("_out = 'same'") == "same"
    interpreter.shutdown()
    records = recorder.records()

    shifted = tuple(record.with_duration(record.duration_ms + 1) for record in records)
    comparison = compare_callback_records(records, shifted)
    assert comparison.semantic_differences == ()
    assert comparison.timing_only_differences
