"""Explicit in-process interpreter factory for native RLM tests."""

from __future__ import annotations

from typing import Any

from fleet_rlm.daytona.interpreter import (
    DAYTONA_EXECUTION_INSTRUCTIONS,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
)
from fleet_rlm.rlm.program import build_native_rlm


def in_process_interpreter_factory() -> DaytonaCodeInterpreter:
    """Return a fresh interpreter for tests that exercise native DSPy execution."""
    return DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())


in_process_interpreter_factory.__dict__["execution_instructions"] = DAYTONA_EXECUTION_INSTRUCTIONS


def build_native_rlm_for_test(**kwargs: Any) -> Any:
    """Build a native RLM with explicit local execution authority."""
    kwargs.setdefault("interpreter_factory", in_process_interpreter_factory)
    return build_native_rlm(**kwargs)


__all__ = ["build_native_rlm_for_test", "in_process_interpreter_factory"]
