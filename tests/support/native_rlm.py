"""Native DSPy local interpreter factory for native RLM tests."""

from __future__ import annotations

from typing import Any

import dspy

from fleet_rlm.daytona.interpreter import DAYTONA_EXECUTION_INSTRUCTIONS
from fleet_rlm.rlm.program import InstructedInterpreterFactory, build_native_rlm

local_interpreter_factory = InstructedInterpreterFactory(
    dspy.LocalInterpreter, execution_instructions=DAYTONA_EXECUTION_INSTRUCTIONS
)


def build_native_rlm_for_test(**kwargs: Any) -> Any:
    """Build a native RLM with explicit local execution authority."""
    kwargs.setdefault("interpreter_factory", local_interpreter_factory)
    return build_native_rlm(**kwargs)


__all__ = ["build_native_rlm_for_test", "local_interpreter_factory"]
