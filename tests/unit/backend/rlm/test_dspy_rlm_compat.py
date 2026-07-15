"""Contracts for the pinned DSPy RLM constructor seam."""

from __future__ import annotations

from typing import Any

import dspy


class _Interpreter:
    @property
    def tools(self) -> dict[str, Any]:
        return {}


def _lookup(value: str) -> str:
    """Return a value through a host tool."""
    return value


def test_build_native_rlm_preserves_product_constructor_inputs() -> None:
    from fleet_rlm.rlm.compat import build_native_rlm

    class TaskSignature(dspy.Signature):
        request: str = dspy.InputField()
        answer: str = dspy.OutputField()

    sub_lm = object()
    interpreter = _Interpreter()
    kwargs: dict[str, Any] = {
        "signature": TaskSignature,
        "max_iters": 7,
        "max_llm_calls": 11,
        "max_output_chars": 2048,
        "tools": [_lookup],
        "sub_lm": sub_lm,
        "interpreter": interpreter,
    }

    first = build_native_rlm(**kwargs)
    second = build_native_rlm(**kwargs)

    assert type(first) is dspy.RLM
    assert first is not second
    assert first.signature is TaskSignature
    assert first.max_iterations == 7
    assert first.max_llm_calls == 11
    assert first.max_output_chars == 2048
    assert first.sub_lm is sub_lm
    assert first._interpreter is interpreter  # noqa: SLF001 - pinned DSPy contract
    assert set(first.tools) == {"_lookup"}
