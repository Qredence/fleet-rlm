"""Deterministic answer oracle for the malformed-action trace request."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fleet_rlm.rlm.program import FleetJSONAdapter
from tests.support.scripted_lm import _IterationActionSignature, _ScriptedLM

FIXTURES = Path(__file__).parents[3] / "fixtures" / "rlm"
EXPECTED = ({"A": 7, "B": 7, "C": 8, "D": 12, "E": 2}, 6, ("D",))


def _interpret(request: str) -> tuple[dict[str, int], int, tuple[str, ...]]:
    registers = dict.fromkeys("ABCDE", 0)
    true_count = 0
    instructions = re.findall(r"(?m)^(\d+)\. (ADD|SUB|COPY|SWAP|IFPOS|IFEVEN|DOUBLE|MOD) (.+)$", request)
    assert [int(number) for number, _, _ in instructions] == list(range(1, 41))

    def execute(operation: str, operands: str) -> None:
        nonlocal true_count
        parts = operands.split()
        x = parts[0].rstrip(":")
        assert x in registers
        if operation in {"IFPOS", "IFEVEN"}:
            condition = registers[x] > 0 if operation == "IFPOS" else registers[x] % 2 == 0
            if condition:
                true_count += 1
                inner_operation, inner_operands = operands.split(": ", 1)[1].split(" ", 1)
                execute(inner_operation, inner_operands)
        elif operation == "ADD":
            registers[x] += int(parts[1])
        elif operation == "SUB":
            registers[x] -= int(parts[1])
        elif operation == "COPY":
            registers[x] = registers[parts[1]]
        elif operation == "SWAP":
            registers[x], registers[parts[1]] = registers[parts[1]], registers[x]
        elif operation == "DOUBLE":
            registers[x] *= 2
        elif operation == "MOD":
            registers[x] %= int(parts[1])
        else:
            raise AssertionError(operation)

    for _, operation, operands in instructions:
        execute(operation, operands)
    largest = max(registers.values())
    return registers, true_count, tuple(name for name, value in registers.items() if value == largest)


@pytest.mark.parametrize("name", ["register_trace_exact.txt"])
def test_register_request_oracle(name: str) -> None:
    request = (FIXTURES / name).read_text()
    assert _interpret(request) == EXPECTED


def test_exact_trace_retains_trailing_text() -> None:
    request = (FIXTURES / "register_trace_exact.txt").read_text()
    assert "</parameter>\n</invoke>" in request
    assert "Want me to:" in request


def test_fenced_python_actions_fail_closed() -> None:
    import dspy
    from dspy.utils.exceptions import AdapterParseError

    lm = _ScriptedLM(["```python\nprint(request)\n```"])
    with pytest.raises(AdapterParseError), dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        dspy.Predict(_IterationActionSignature)(iteration="1/12")
    assert len(lm.calls) == 3
