"""MLflow span contracts for the sandbox-execution phase of the interpreter."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend


@pytest.fixture
def fleet_trace_active() -> Iterator[None]:
    """Open the fleet turn-trace gate so phase spans engage the (fake) MLflow."""
    from fleet_rlm.observability import turn_tracing

    token = turn_tracing._fleet_trace_active.set(True)
    yield
    turn_tracing._fleet_trace_active.reset(token)


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(start_span_names=[], span_inputs=[], span_outputs=[], span_statuses=[])

    class _FakeSpan:
        def set_inputs(self, payload: dict[str, object]) -> None:
            calls.span_inputs.append(payload)

        def set_outputs(self, payload: dict[str, object]) -> None:
            calls.span_outputs.append(payload)

        def set_status(self, status: str) -> None:
            calls.span_statuses.append(status)

    active_span = _FakeSpan()

    @contextmanager
    def start_span(*, name: str = "span", span_type: Any = None, **_kwargs: Any) -> Iterator[Any]:
        del span_type
        calls.start_span_names.append(name)
        yield active_span

    mlflow = ModuleType("mlflow")
    mlflow.start_span = start_span  # type: ignore[attr-defined]
    mlflow.get_current_active_span = lambda: active_span  # type: ignore[attr-defined]

    entities = ModuleType("mlflow.entities")
    entities.SpanType = SimpleNamespace(CHAIN="CHAIN")  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.entities", entities)
    return calls


def test_sandbox_execute_span_emits_bounded_metadata(monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    result = interpreter.execute("_out = 'hello'")

    assert result == "hello"
    assert calls.start_span_names == ["sandbox.execute"]
    assert calls.span_inputs[0] == {
        "iteration": 1,
        "code_chars": len("_out = 'hello'"),
        "variable_count": 0,
        "code_preview": "_out = 'hello'",
    }
    assert calls.span_outputs[0] == {
        "path": "InProcessInterpreterBackend",
        "result_kind": "output",
        "stdout_chars": 5,
        "output_preview": "hello",
        "phase_status": "completed",
    }


def test_sandbox_execute_span_tracks_iteration_and_repair_kind(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    interpreter.execute("_out = 'first'")
    repair = interpreter.execute("missing_name + 1")

    assert isinstance(repair, str) and repair.startswith("[Error]")
    assert calls.span_inputs[1]["iteration"] == 2
    assert calls.span_outputs[1]["result_kind"] == "repair_error"
    assert calls.span_outputs[1]["repair_category"] == "NameError"
    assert calls.span_outputs[1]["execution_status"] == "recovered_error"
    assert calls.span_outputs[1]["phase_status"] == "failed"
    assert calls.span_statuses == ["ERROR"]


def test_sandbox_rejects_oversized_intermediate_code_before_backend_execution(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)

    class Backend:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            self.calls += 1
            return "unexpected"

        def close(self) -> None:
            return None

    backend = Backend()
    interpreter = DaytonaCodeInterpreter(backend=backend, max_code_chars=8)

    result = interpreter.execute("value = 1\n_out = value")

    assert isinstance(result, str) and result.startswith("[Error] Intermediate code is too large")
    assert backend.calls == 0
    assert calls.span_outputs[0]["result_kind"] == "repair_error"
    assert calls.span_outputs[0]["repair_category"] == "code_too_large"
    assert calls.span_outputs[0]["phase_status"] == "failed"


def test_sandbox_execute_span_marks_failed_phase_without_suppressing(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)

    class _ExplodingBackend:
        def run(self, code: str, variables: dict[str, object] | None = None) -> str:
            del code, variables
            raise RuntimeError("backend boom")

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=_ExplodingBackend())

    with pytest.raises(Exception, match="backend boom"):
        interpreter.execute("_out = 'never'")

    assert calls.start_span_names == ["sandbox.execute"]
    assert calls.span_outputs[0]["phase_status"] == "failed"


def test_sandbox_execute_without_active_trace_is_noop() -> None:
    """No fake mlflow: real mlflow has no active span, so tracing is a no-op."""
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    assert interpreter.execute("_out = 'untraced'") == "untraced"


def test_sandbox_execute_span_reports_broker_rtt_breakdown(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)

    class _FakeBroker:
        def __init__(self) -> None:
            self.last_execution_stats = {"poll_count": 12, "tool_call_count": 2}

        def execute_with_callbacks(self, *, run_code: Any, tool_executor: Any) -> Any:
            del tool_executor
            return run_code()

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter._http_broker = _FakeBroker()

    result = interpreter.execute("_out = 'via broker'")

    assert result == "via broker"
    outputs = calls.span_outputs[0]
    assert outputs["path"] == "http_broker"
    assert outputs["poll_count"] == 12
    assert outputs["tool_call_count"] == 2
    assert outputs["ensure_bindings_ms"] >= 0
    assert outputs["execute_ms"] >= 0
    assert outputs["phase_status"] == "completed"
