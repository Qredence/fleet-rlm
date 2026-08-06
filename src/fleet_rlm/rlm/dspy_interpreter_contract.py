"""DSPy 3.3.x interpreter injection and FinalOutput contract.

Public ``CodeInterpreter`` only requires ``tools``, ``start``, ``execute``, and
``shutdown``. Stock ``dspy.RLM`` additionally probes custom interpreters during
``_inject_execution_context``:

1. ``interpreter.tools.update(execution_tools)``
2. If ``hasattr(interpreter, "output_fields")``: assign signature output metadata
3. If ``hasattr(interpreter, "_tools_registered")``: set ``_tools_registered = False``

Fleet adapters must re-register host tools and SUBMIT when that flag is false (or
the HTTP broker has not yet been started for this reinjection cycle).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from dspy import CodeInterpreter, FinalOutput

PUBLIC_FINAL_OUTPUT_LABEL = "FINAL submitted"


def copy_output_fields(
    output_fields: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Return an independent copy of signature output metadata for interpreter state."""
    if output_fields is None:
        return None
    return deepcopy(output_fields)


def initial_tools_registered() -> bool:
    """Return the inject-cycle registration flag before the first reinjection."""
    return False


def needs_tool_reinjection(*, tools_registered: bool, http_broker_ready: bool) -> bool:
    """Return whether the adapter must register tools and SUBMIT for this cycle."""
    if not tools_registered:
        return True
    return not http_broker_ready


def mark_tools_registered() -> bool:
    """Return the registration flag after tools and SUBMIT are bound."""
    return True


def wrap_final_output(value: Any) -> FinalOutput:
    """Wrap a SUBMIT payload in the pinned DSPy terminate signal."""
    return FinalOutput(value)


def is_final_output(value: Any) -> bool:
    """Return whether ``execute()`` returned a successful SUBMIT terminate signal."""
    return isinstance(value, FinalOutput)


__all__ = [
    "PUBLIC_FINAL_OUTPUT_LABEL",
    "CodeInterpreter",
    "FinalOutput",
    "copy_output_fields",
    "initial_tools_registered",
    "is_final_output",
    "mark_tools_registered",
    "needs_tool_reinjection",
    "wrap_final_output",
]
