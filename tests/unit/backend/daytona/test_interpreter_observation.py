"""Observation contracts for the product-owned Daytona interpreter boundary."""

from __future__ import annotations

import pytest

from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.in_process import InProcessInterpreterBackend
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter
from fleet_rlm.rlm.events import RLMCode, RLMOutput, StepFinished, StepStarted


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
    assert "secret-value" not in observed[2].output
    assert "/home/daytona" not in observed[2].output
