"""Execution-output bounding contracts for the interpreter feedback loop."""

from __future__ import annotations

import json
import time

import pytest
from dspy import FinalOutput
from dspy.primitives.code_interpreter import CodeExecutionError

from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    OutputCallback,
    sandbox_backend,
)
from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.output_contract import FleetOutputContract, OutputField


def test_typed_submit_size_feedback_is_recoverable_and_matches_declared_json() -> None:
    answer = "é" * 4
    limit = len(json.dumps({"answer": answer}, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        output_fields=[{"name": "answer", "type": "str"}],
    )
    interpreter.bind_output_contract(FleetOutputContract((OutputField("answer", True),), limit))

    with pytest.raises(CodeExecutionError, match=f"{limit + 1} > {limit} characters") as error:
        interpreter.execute(f"SUBMIT(answer={answer + 'é'!r})")
    assert answer not in str(error.value)

    result = interpreter.execute(f"SUBMIT(answer={answer!r})")
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": answer}


def test_turn_output_budget_is_shared_and_fail_closed() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), execution_output_cap=400)
    observed: list[object] = []
    budget = TurnBudget(
        deadline=time.monotonic() + 60,
        limits=BudgetLimits(execution_output_bytes=3),
    )
    interpreter.bind_observer(observed.append)
    interpreter.bind_turn_budget(budget)

    with pytest.raises(TurnBudgetExhausted) as caught:
        interpreter.execute("_out = 'abcd'")

    assert caught.value.dimension == BudgetDimension.EXECUTION_OUTPUT_BYTES
    assert budget.snapshot()[BudgetDimension.EXECUTION_OUTPUT_BYTES.value] == 0
    assert not any(getattr(item, "output", "") == "Execution failed" for item in observed)


def test_error_feedback_includes_capped_stderr() -> None:
    class _StderrBackend:
        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            """
            Simulate a failed backend execution with an undefined-name error.

            Returns:
                BackendExecutionResult: An execution result with a fixed `NameError`
                    message and 5,000-character stderr output.
            """
            del code, variables, on_stdout
            return BackendExecutionResult(
                stdout="", error="NameError: name 'missing' is not defined", stderr="s" * 5000
            )

        def close(self) -> None:
            return None

    interpreter = DaytonaCodeInterpreter(backend=_StderrBackend(), execution_output_cap=300)

    with pytest.raises(CodeExecutionError) as caught:
        interpreter.execute("missing + 1")
    result = str(caught.value)

    assert isinstance(result, str)
    assert result.startswith("NameError")
    assert "stderr:" in result
    assert len(result) < 450
    assert "characters omitted" in result


def test_typed_stdout_is_capped_before_returning_feedback() -> None:
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), execution_output_cap=300)
    result = interpreter.execute("print('head' + 'x' * 5_000 + 'tail')")
    assert result.startswith("head")
    assert result.endswith("tail\n")
    assert "characters omitted" in result
    assert len(result) < 400
    interpreter.shutdown()


def test_sandbox_backend_retains_timeout_for_broker_execution() -> None:
    backend = sandbox_backend(object(), timeout_s=45)
    assert backend.timeout_s == 45

    unbounded = sandbox_backend(object(), timeout_s=None)
    assert unbounded.timeout_s is None
