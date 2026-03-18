"""Shared helpers for recognizing tool/runtime errors in trajectories."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_ERROR_TOKENS: tuple[str, ...] = (
    "[error]",
    "traceback",
    "runtimeerror",
    "codeinterpretererror",
    "syntaxerror",
    "execution error in",
    "exception",
    "failed",
    "error:",
)

_FALSE_POSITIVE_TOKENS: tuple[str, ...] = (
    "no error",
    "without error",
    "0 errors",
    "zero errors",
)


def looks_like_tool_error(value: Any) -> bool:
    """Return True when *value* resembles a tool/runtime failure payload."""
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(token in text for token in _FALSE_POSITIVE_TOKENS):
        return False
    return any(token in text for token in _ERROR_TOKENS)


def _iter_trajectory_values(trajectory: Any) -> Iterable[Any]:
    if not isinstance(trajectory, dict):
        return ()

    def _generator() -> Iterable[Any]:
        for key, value in trajectory.items():
            if str(key).startswith(("output_", "observation_", "error_")):
                yield value

        structured_steps: list[Any] = []
        maybe_steps = trajectory.get("steps")
        if isinstance(maybe_steps, list):
            structured_steps.extend(maybe_steps)
        maybe_trajectory = trajectory.get("trajectory")
        if isinstance(maybe_trajectory, list):
            structured_steps.extend(maybe_trajectory)

        for step in structured_steps:
            if not isinstance(step, dict):
                continue
            for field_name in ("output", "observation", "error"):
                field_value = step.get(field_name)
                if field_value is not None:
                    yield field_value

    return _generator()


def count_tool_errors(trajectory: Any) -> int:
    """Count error-like tool observations within a trajectory payload."""
    return sum(
        1
        for value in _iter_trajectory_values(trajectory)
        if looks_like_tool_error(value)
    )


def trajectory_has_tool_errors(trajectory: Any) -> bool:
    """Return True when any observation in *trajectory* looks erroneous."""
    return count_tool_errors(trajectory) > 0
