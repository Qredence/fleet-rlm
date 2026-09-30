"""MLflow span contracts for the sandbox-execution phase of the interpreter."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    OutputCallback,
)
from fleet_rlm.rlm.budget import BudgetLimits, TurnBudget, TurnBudgetExhausted


@pytest.fixture
def fleet_trace_active() -> Iterator[None]:
    """Open the fleet turn-trace gate so phase spans engage the (fake) MLflow."""
    from fleet_rlm.observability import tracing as turn_tracing

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
        "ensure_bindings_ms": 0,
        "execute_ms": 0,
    }


@pytest.mark.parametrize(
    ("cause_type", "expected_category"),
    [("BrokerExecutionTimeout", "timeout"), ("BrokerExecutionError", "adapter_error")],
)
def test_sandbox_execute_span_classifies_broker_failure_and_keeps_it_terminal(
    monkeypatch: pytest.MonkeyPatch,
    fleet_trace_active: None,
    cause_type: str,
    expected_category: str,
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)

    class FailingBackend:
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            del code, variables, on_stdout
            raise DaytonaAdapterError("safe failure", cause_type=cause_type)

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=FailingBackend())
    with pytest.raises(DaytonaAdapterError):
        interpreter.execute("print('never')")

    assert calls.span_outputs[0]["failure_category"] == expected_category
    assert calls.span_outputs[0]["phase_status"] == "failed"


def test_sandbox_execute_span_classifies_budget_exhaustion(
    monkeypatch: pytest.MonkeyPatch, fleet_trace_active: None
) -> None:
    del fleet_trace_active
    calls = _install_fake_mlflow(monkeypatch)
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    interpreter.bind_turn_budget(
        TurnBudget(deadline=time.monotonic() + 60, limits=BudgetLimits(execution_output_bytes=1))
    )

    with pytest.raises(TurnBudgetExhausted):
        interpreter.execute("_out = 'too large'")

    assert calls.span_outputs[0]["failure_category"] == "budget_execution_output_bytes"
    assert calls.span_outputs[0]["phase_status"] == "failed"


def test_sandbox_execute_without_active_trace_is_noop() -> None:
    """No fake mlflow: real mlflow has no active span, so tracing is a no-op."""
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

    assert interpreter.execute("_out = 'untraced'") == "untraced"
