"""Pinned DSPy RLM constructor compatibility."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import dspy

if TYPE_CHECKING:
    from fleet_rlm.skills.capabilities import RLMTool


def build_native_rlm(
    *,
    signature: type[dspy.Signature] | str,
    max_iters: int,
    max_llm_calls: int,
    max_output_chars: int,
    tools: Sequence[RLMTool] | None = None,
    sub_lm: dspy.LM | None = None,
    interpreter: Any = None,
    verbose: bool = False,
) -> dspy.RLM:  # ty: ignore[invalid-type-form] - DSPy @experimental obscures the class type
    """Build a fresh native RLM using the constructor spelling in DSPy 3.3.0b1."""
    return dspy.RLM(
        signature,
        max_iterations=max_iters,
        max_llm_calls=max_llm_calls,
        max_output_chars=max_output_chars,
        verbose=verbose,
        tools=list(tools) if tools is not None else None,
        sub_lm=sub_lm,
        interpreter=interpreter,
    )
