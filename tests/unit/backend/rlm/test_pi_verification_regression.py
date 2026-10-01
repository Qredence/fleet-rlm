"""Scripted native-RLM replay of the costly verification trace."""

from __future__ import annotations

import asyncio
from typing import Any

import dspy
import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.interpreter import (
    BackendExecutionResult,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    OutputCallback,
)
from fleet_rlm.rlm.program import FleetJSONAdapter
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM

REQUEST = "Tell me the 1492252th digits after the decimal point of Pi"
# Independently checked at https://api.pi.delivery/v1/pi?start=1492252&numberOfDigits=1.
EXPECTED_DIGIT = "5"


def test_pi_answer_submits_after_bounded_verification() -> None:
    class Actions:
        def __init__(self) -> None:
            self.codes: list[str] = []

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            if not self.codes:
                code = (
                    f"pi_digit = {EXPECTED_DIGIT!r}\n"
                    f"reference_digit = {EXPECTED_DIGIT!r}\n"
                    "assert pi_digit == reference_digit\n"
                    "print(pi_digit)"
                )
            else:
                code = "SUBMIT(answer=pi_digit)"
            self.codes.append(code)
            return dspy.Prediction(reasoning="Use the bounded check, then submit", code=code)

    actions = Actions()
    rlm = dspy.RLM("request -> answer: str", max_iters=2)
    rlm.generate_action = actions
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    try:
        prediction = asyncio.run(rlm.acall(interpreter_factory=lambda: interpreter, request=REQUEST))
    finally:
        interpreter.shutdown()

    assert prediction.answer == EXPECTED_DIGIT
    assert prediction.trajectory[0]["output"].strip() == EXPECTED_DIGIT
    assert actions.codes[-1] == "SUBMIT(answer=pi_digit)"
    assert len(actions.codes) == 2
    assert all("Decimal" not in code and "Gauss" not in code for code in actions.codes)


def test_valid_pi_submit_action_needs_no_parse_repair() -> None:
    lm = _ScriptedLM(['{"reasoning":"bounded check passed","code":"SUBMIT(answer=\'5\')"}'])

    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        action = dspy.Predict(_IterationActionSignature)(iteration="2/2")

    assert action.code == "SUBMIT(answer='5')"
    assert len(lm.calls) == 1


def test_broker_timeout_stops_native_rlm_and_closes_interpreter() -> None:
    class Actions:
        calls = 0

        async def acall(self, **_kwargs: Any) -> dspy.Prediction:
            self.calls += 1
            return dspy.Prediction(reasoning="compute", code="print('working')")

    class TimedOutBackend:
        closed = False

        def run(
            self,
            code: str,
            variables: dict[str, object] | None = None,
            *,
            on_stdout: OutputCallback | None = None,
        ) -> BackendExecutionResult:
            del code, variables, on_stdout
            raise DaytonaAdapterError("sandbox execution request timed out", cause_type="BrokerExecutionTimeout")

        def close(self) -> None:
            self.closed = True

    actions = Actions()
    backend = TimedOutBackend()
    interpreter = DaytonaCodeInterpreter(backend=backend)
    rlm = dspy.RLM("request -> answer: str", max_iters=2)
    rlm.generate_action = actions
    try:
        with pytest.raises(DaytonaAdapterError):
            asyncio.run(rlm.acall(interpreter_factory=lambda: interpreter, request=REQUEST))
    finally:
        interpreter.shutdown()

    assert actions.calls == 1
    assert backend.closed
