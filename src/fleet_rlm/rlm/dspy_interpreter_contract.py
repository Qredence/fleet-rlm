"""DSPy 3.3.x interpreter injection and FinalOutput contract.

DSPy injects invocation-scoped tools through the public ``tools`` mapping and
assigns typed ``output_fields`` metadata before each execution.  Fleet tracks
the desired binding generation and refreshes its broker/namespace from those
public mutations; it does not depend on DSPy implementation state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from dspy import CodeExecutionError, CodeInterpreter, CodeInterpreterError, FinalOutput

PUBLIC_FINAL_OUTPUT_LABEL = "FINAL submitted"

# Keep this text in one place.  DSPy copies callable metadata into its native
# action Signature exactly once at RLM construction time.
DAYTONA_EXECUTION_INSTRUCTIONS = (
    "Execution runs in isolated Python. The Python namespace persists across actions in one invocation. "
    "Host Tools are callable Python functions through Fleet's local mediation seam. "
    "Ordinary stdout is observable. Use the typed keyword `SUBMIT` for final completion."
)


def copy_output_fields(
    output_fields: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Return an independent copy of signature output metadata for interpreter state."""
    if output_fields is None:
        return None
    return deepcopy(output_fields)


def needs_binding_refresh(
    *,
    desired_generation: int,
    installed_generation: int,
    broker_ready: bool,
) -> bool:
    """Return whether Fleet must refresh the complete current binding set."""
    return desired_generation != installed_generation or not broker_ready


def wrap_final_output(value: Any) -> FinalOutput:
    """Wrap a SUBMIT payload in the pinned DSPy terminate signal."""
    return FinalOutput(value)


def is_final_output(value: Any) -> bool:
    """Return whether ``execute()`` returned a successful SUBMIT terminate signal."""
    return isinstance(value, FinalOutput)


__all__ = [
    "DAYTONA_EXECUTION_INSTRUCTIONS",
    "PUBLIC_FINAL_OUTPUT_LABEL",
    "CodeExecutionError",
    "CodeInterpreter",
    "CodeInterpreterError",
    "FinalOutput",
    "copy_output_fields",
    "is_final_output",
    "needs_binding_refresh",
    "wrap_final_output",
]
